/**
 * Voice client for the assessment preview (ADR 0039 node [1], voice channel).
 *
 * Starts an ElevenLabs voice session (mic + speech + TTS) whose brain is our
 * custom-LLM endpoint, accumulates the spoken transcript turn by turn, and on
 * end feeds the same findings step the typed flow uses. The session ends on the
 * owner's action — there is no spoken completion marker (see the voice addendum
 * in assessment-llm.ts).
 */

import { Conversation } from '@elevenlabs/client'
import { escapeHtml } from '../lib/api/helpers'

interface Turn {
  speaker: 'owner' | 'operator'
  text: string
}

type VoiceSession = Awaited<ReturnType<typeof Conversation.startSession>>

const turns: Turn[] = []
let session: VoiceSession | null = null
let ending = false

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id)
  if (!node) throw new Error(`missing #${id}`)
  return node as T
}

function strField(value: unknown, key: string): string | undefined {
  if (typeof value === 'object' && value !== null && key in value) {
    const v = (value as Record<string, unknown>)[key]
    return typeof v === 'string' ? v : undefined
  }
  return undefined
}

function inline(s: string): string {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^_])_([^_]+?)_(?=[^_]|$)/g, '$1<em>$2</em>')
}

function renderMarkdown(md: string): string {
  const lines = md.split('\n')
  const out: string[] = []
  let inList = false
  const closeList = (): void => {
    if (inList) {
      out.push('</ul>')
      inList = false
    }
  }
  for (const rawLine of lines) {
    const line = rawLine.trimEnd()
    if (line.trim() === '') {
      closeList()
      continue
    }
    if (line.startsWith('### ')) {
      closeList()
      out.push(`<h4>${inline(line.slice(4))}</h4>`)
    } else if (line.startsWith('## ')) {
      closeList()
      out.push(`<h3>${inline(line.slice(3))}</h3>`)
    } else if (line.startsWith('# ')) {
      closeList()
      out.push(`<h2>${inline(line.slice(2))}</h2>`)
    } else if (line.startsWith('> ')) {
      closeList()
      out.push(`<blockquote>${inline(line.slice(2))}</blockquote>`)
    } else if (line.startsWith('- ')) {
      if (!inList) {
        out.push('<ul>')
        inList = true
      }
      out.push(`<li>${inline(line.slice(2))}</li>`)
    } else {
      closeList()
      out.push(`<p>${inline(line)}</p>`)
    }
  }
  closeList()
  return out.join('\n')
}

function appendBubble(speaker: 'owner' | 'operator', text: string): void {
  const chat = el<HTMLDivElement>('chat')
  const wrap = document.createElement('div')
  wrap.className = `bubble bubble-${speaker}`
  const who = document.createElement('div')
  who.className = 'bubble-who'
  who.textContent = speaker === 'operator' ? 'Assessment' : 'You'
  const body = document.createElement('div')
  body.className = 'bubble-text'
  body.textContent = text
  wrap.appendChild(who)
  wrap.appendChild(body)
  chat.appendChild(wrap)
  chat.scrollTop = chat.scrollHeight
}

function setStatus(text: string): void {
  el<HTMLDivElement>('voice-status').textContent = text
}

async function drawFindings(): Promise<void> {
  const report = el<HTMLElement>('report')
  const reportBody = el<HTMLDivElement>('report-body')
  report.hidden = false
  reportBody.innerHTML = '<p class="muted">Drafting your findings…</p>'
  report.scrollIntoView({ behavior: 'smooth' })
  try {
    const res = await fetch('/api/assessment/findings', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ turns }),
    })
    const data: unknown = await res.json()
    const findings = strField(data, 'findings')
    if (!res.ok || !findings) {
      reportBody.innerHTML = `<p class="muted">${escapeHtml(strField(data, 'error') ?? 'Could not draft findings.')}</p>`
      return
    }
    reportBody.innerHTML = renderMarkdown(findings)
  } catch {
    reportBody.innerHTML = '<p class="muted">Connection problem drafting findings.</p>'
  }
}

async function endVoice(): Promise<void> {
  if (ending) return
  ending = true
  el<HTMLButtonElement>('voice-end').disabled = true
  setStatus('Wrapping up…')
  el<HTMLDivElement>('voice-controls').hidden = true
  try {
    if (session) await session.endSession()
  } catch {
    /* ignore */
  }
  const ownerTurns = turns.filter((t) => t.speaker === 'owner').length
  if (ownerTurns >= 1 && turns.length >= 2) {
    await drawFindings()
    return
  }
  // The session ended before a real exchange — never draft findings from a
  // greeting alone (that produced a confusing refusal). Offer a retry instead.
  const report = el<HTMLElement>('report')
  const reportBody = el<HTMLDivElement>('report-body')
  report.hidden = false
  reportBody.innerHTML =
    '<p class="muted">The conversation ended before we captured enough to draft findings. Refresh and try again — and if it keeps dropping right after the greeting, the voice connection is the issue, not you.</p>'
}

async function startVoice(): Promise<void> {
  el<HTMLButtonElement>('start-voice').hidden = true
  el<HTMLButtonElement>('start-btn').hidden = true
  el<HTMLDivElement>('intro').hidden = true
  el<HTMLDivElement>('chat').hidden = false
  el<HTMLDivElement>('voice-controls').hidden = false
  setStatus('Connecting… allow microphone access when asked.')
  try {
    const res = await fetch('/api/assessment/voice-token')
    const data: unknown = await res.json()
    const signedUrl = strField(data, 'signedUrl')
    if (!signedUrl) {
      setStatus('Voice is unavailable right now.')
      return
    }
    session = await Conversation.startSession({
      signedUrl,
      onConnect: () => setStatus('Connected. Start talking when you’re ready.'),
      onModeChange: ({ mode }) =>
        setStatus(mode === 'speaking' ? 'Assessment is speaking…' : 'Listening…'),
      onMessage: ({ message, source }: { message: string; source: 'user' | 'ai' }) => {
        if (!message) return
        const speaker: 'owner' | 'operator' = source === 'ai' ? 'operator' : 'owner'
        appendBubble(speaker, message)
        turns.push({ speaker, text: message })
      },
      onError: (msg: string) => setStatus(`Error: ${msg}`),
      onDisconnect: () => {
        void endVoice()
      },
    })
  } catch {
    setStatus('Could not start the voice session. Please try again.')
  }
}

function init(): void {
  el<HTMLButtonElement>('start-voice').addEventListener('click', () => void startVoice())
  el<HTMLButtonElement>('voice-end').addEventListener('click', () => void endVoice())
}

init()
