/**
 * Client for the live web assessment preview (ADR 0039 nodes 1 + 2).
 *
 * Drives the typed conversation against /api/assessment/turn, and on completion
 * fetches the evidence-bound findings from /api/assessment/findings and renders
 * the report. Keeps the visible turns in memory and round-trips the full list
 * each turn — no persistence yet (dogfood preview).
 */

import { escapeHtml } from '../lib/api/helpers'

interface Turn {
  speaker: 'owner' | 'operator'
  text: string
}

const turns: Turn[] = []

/**
 * Signed session token issued by the server on the opening turn (ADR 0039 /
 * 2026-06-08 hardening). Echoed on every subsequent turn so the server can
 * enforce its per-session ceiling. Null until the opener returns it.
 */
let session: string | null = null

function el<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id)
  if (!node) throw new Error(`missing #${id}`)
  return node as T
}

/** Narrow a parsed JSON value's string field without casting the whole payload to `any`. */
function strField(value: unknown, key: string): string | undefined {
  if (typeof value === 'object' && value !== null && key in value) {
    const v = (value as Record<string, unknown>)[key]
    return typeof v === 'string' ? v : undefined
  }
  return undefined
}

function boolField(value: unknown, key: string): boolean {
  return (
    typeof value === 'object' && value !== null && (value as Record<string, unknown>)[key] === true
  )
}

function inline(s: string): string {
  return escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^_])_([^_]+?)_(?=[^_]|$)/g, '$1<em>$2</em>')
}

/** Minimal, safe markdown → HTML for the findings shape (headings, lists, blockquotes, paragraphs). */
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

function setThinking(on: boolean): void {
  el<HTMLDivElement>('thinking').hidden = !on
  if (on) {
    const chat = el<HTMLDivElement>('chat')
    chat.scrollTop = chat.scrollHeight
  }
}

function setComposerEnabled(on: boolean): void {
  el<HTMLTextAreaElement>('owner-input').disabled = !on
  el<HTMLButtonElement>('send-btn').disabled = !on
}

async function operatorTurn(): Promise<void> {
  setThinking(true)
  setComposerEnabled(false)
  try {
    const res = await fetch('/api/assessment/turn', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(session ? { turns, session } : { turns }),
    })
    const data: unknown = await res.json()
    const message = strField(data, 'message')
    if (!res.ok || !message) {
      appendBubble('operator', strField(data, 'error') ?? 'Something went wrong. Please try again.')
      setComposerEnabled(true)
      return
    }
    // The opening turn carries the freshly minted session token; hold onto it
    // for every subsequent turn. Later turns omit it from the response.
    const issued = strField(data, 'session')
    if (issued) session = issued
    appendBubble('operator', message)
    turns.push({ speaker: 'operator', text: message })
    if (boolField(data, 'done')) {
      await finish()
    } else {
      setComposerEnabled(true)
      el<HTMLTextAreaElement>('owner-input').focus()
    }
  } catch {
    appendBubble('operator', 'Connection problem. Please try again.')
    setComposerEnabled(true)
  } finally {
    setThinking(false)
  }
}

async function finish(): Promise<void> {
  const composer = el<HTMLFormElement>('composer')
  composer.hidden = true
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

function onOwnerSubmit(event: Event): void {
  event.preventDefault()
  const input = el<HTMLTextAreaElement>('owner-input')
  const text = input.value.trim()
  if (text === '') return
  appendBubble('owner', text)
  turns.push({ speaker: 'owner', text })
  input.value = ''
  void operatorTurn()
}

function start(): void {
  el<HTMLButtonElement>('start-btn').hidden = true
  el<HTMLDivElement>('intro').hidden = true
  el<HTMLDivElement>('chat').hidden = false
  el<HTMLFormElement>('composer').hidden = false
  void operatorTurn() // empty turns → operator opening
}

function init(): void {
  el<HTMLButtonElement>('start-btn').addEventListener('click', start)
  el<HTMLFormElement>('composer').addEventListener('submit', onOwnerSubmit)
  el<HTMLTextAreaElement>('owner-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      el<HTMLFormElement>('composer').requestSubmit()
    }
  })
}

init()
