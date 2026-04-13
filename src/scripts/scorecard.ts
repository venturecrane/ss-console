/**
 * Client-side quiz logic for the Operations Health Scorecard.
 *
 * Manages: step navigation, answer storage, progress bar, scoring,
 * results rendering, and form submission.
 *
 * Scoring constants are read from embedded JSON (#scorecard-data) to
 * avoid duplication with the server module. Only dimension summation
 * and lookup are done client-side — pain score and auto-stage are
 * server-only concerns.
 */

// ---------------------------------------------------------------------------
// Types (mirrors server types for the embedded data)
// ---------------------------------------------------------------------------

interface ScoreThreshold {
  min: number
  max: number
  label: string
  displayLabel: string
  color: string
}

interface DimensionDef {
  id: string
  label: string
  icon: string
  sectionHeader: string
}

interface QuestionOption {
  key: string
  score: number
  text: string
}

interface ScoredQuestion {
  id: string
  dimension: string
  text: string
  options: QuestionOption[]
}

interface Description {
  needs_attention: string
  room_to_grow: string
  getting_there: string
  strong: string
}

interface EmbeddedData {
  dimensions: DimensionDef[]
  questions: ScoredQuestion[]
  scaledScores: readonly number[]
  scoreThresholds: ScoreThreshold[]
  descriptions: Record<string, Description>
}

interface DimensionResult {
  id: string
  label: string
  scaled: number
  scoreLabel: string
  displayLabel: string
  color: string
  description: string
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let data: EmbeddedData
let currentStep = 0 // 0=landing, 1..21=questions, 22=gate, 23=results
let startedAt = 0
const answers: Record<string, string | number> = {} // question id → answer value
const TOTAL_STEPS = 21 // 3 context + 18 scored

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  const dataEl = document.getElementById('scorecard-data')
  if (!dataEl?.textContent) return
  data = JSON.parse(dataEl.textContent) as EmbeddedData

  // Landing CTA
  document.querySelectorAll('[data-action="start"]').forEach((btn) => {
    btn.addEventListener('click', () => {
      startedAt = Date.now()
      goToStep(1)
    })
  })

  // Back/Next navigation
  document.getElementById('btn-back')?.addEventListener('click', () => {
    if (currentStep > 1) goToStep(currentStep - 1)
  })
  document.getElementById('btn-next')?.addEventListener('click', () => {
    if (currentStep < TOTAL_STEPS) {
      goToStep(currentStep + 1)
    } else if (currentStep === TOTAL_STEPS) {
      goToStep(22) // email gate
    }
  })

  // Answer card selection (delegated)
  document.getElementById('quiz')?.addEventListener('click', (e) => {
    const card = (e.target as HTMLElement).closest('[data-answer]') as HTMLElement | null
    if (!card) return
    const step = card.closest('[data-step]') as HTMLElement | null
    if (!step) return
    selectAnswer(step, card)
  })

  // Email gate form
  document.getElementById('gate-form')?.addEventListener('submit', (e) => {
    e.preventDefault()
    submitScorecard()
  })
})

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

function goToStep(step: number) {
  // Hide all sections
  document.getElementById('landing')?.classList.add('hidden')
  document.getElementById('quiz')?.classList.add('hidden')
  document.getElementById('email-gate')?.classList.add('hidden')
  document.getElementById('results')?.classList.add('hidden')

  // Hide all question steps
  document.querySelectorAll('[data-step]').forEach((el) => el.classList.add('hidden'))

  const progressWrapper = document.getElementById('progress-wrapper')
  const progressFill = document.getElementById('progress-fill')
  const progressText = document.getElementById('progress-text')
  const progressPct = document.getElementById('progress-pct')
  const quizNav = document.getElementById('quiz-nav')

  if (step === 0) {
    document.getElementById('landing')?.classList.remove('hidden')
    progressWrapper?.classList.add('hidden')
    quizNav?.classList.add('hidden')
  } else if (step >= 1 && step <= TOTAL_STEPS) {
    document.getElementById('quiz')?.classList.remove('hidden')
    progressWrapper?.classList.remove('hidden')
    quizNav?.classList.remove('hidden')

    const stepEl = document.querySelector(`[data-step="${step}"]`)
    stepEl?.classList.remove('hidden')

    // Update progress
    const pct = Math.round((step / TOTAL_STEPS) * 100)
    if (progressFill) progressFill.style.width = `${pct}%`
    if (progressText) progressText.textContent = `Question ${step} of ${TOTAL_STEPS}`
    if (progressPct) progressPct.textContent = `${pct}%`

    // Update Next button state
    updateNextButton(step)

    // Update Back button visibility
    const backBtn = document.getElementById('btn-back')
    if (backBtn) backBtn.classList.toggle('invisible', step === 1)
  } else if (step === 22) {
    document.getElementById('email-gate')?.classList.remove('hidden')
    if (progressFill) progressFill.style.width = '100%'
    if (progressText) progressText.textContent = 'Done!'
    if (progressPct) progressPct.textContent = '100%'
    quizNav?.classList.add('hidden')
  } else if (step === 23) {
    document.getElementById('results')?.classList.remove('hidden')
    progressWrapper?.classList.add('hidden')
    quizNav?.classList.add('hidden')
  }

  currentStep = step
  window.scrollTo({ top: 0, behavior: 'instant' })
}

// ---------------------------------------------------------------------------
// Answer selection
// ---------------------------------------------------------------------------

function selectAnswer(stepEl: HTMLElement, card: HTMLElement) {
  // Clear previous selection
  stepEl.querySelectorAll('[data-answer]').forEach((c) => {
    c.classList.remove('border-l-primary', 'bg-blue-50', 'text-primary', 'border-l-4')
    c.classList.add('border-slate-200')
  })

  // Select new
  card.classList.remove('border-slate-200')
  card.classList.add('border-l-primary', 'bg-blue-50', 'text-primary', 'border-l-4')

  // Store answer
  const stepNum = parseInt(stepEl.dataset.step || '0')
  const answerValue = card.dataset.answer || ''

  if (stepNum <= 3) {
    // Context question — store the value string
    const contextIds = ['vertical', 'employee_range', 'role']
    answers[contextIds[stepNum - 1]] = answerValue
  } else {
    // Scored question — store numeric score
    answers[`q${stepNum - 3}`] = parseInt(answerValue)
  }

  updateNextButton(stepNum)
}

function updateNextButton(step: number) {
  const nextBtn = document.getElementById('btn-next')
  if (!nextBtn) return

  let hasAnswer = false
  if (step <= 3) {
    const contextIds = ['vertical', 'employee_range', 'role']
    hasAnswer = answers[contextIds[step - 1]] !== undefined
  } else {
    hasAnswer = answers[`q${step - 3}`] !== undefined
  }

  nextBtn.classList.toggle('opacity-50', !hasAnswer)
  nextBtn.classList.toggle('pointer-events-none', !hasAnswer)
}

// ---------------------------------------------------------------------------
// Scoring (minimal client-side — reads constants from embedded JSON)
// ---------------------------------------------------------------------------

function computeClientScores(): {
  dimensions: DimensionResult[]
  overall: number
  overallDisplayLabel: string
  overallColor: string
} {
  const dimensions: DimensionResult[] = data.dimensions.map((dim) => {
    const dimQuestions = data.questions.filter((q) => q.dimension === dim.id)
    const answered = dimQuestions.filter((q) => ((answers[q.id] as number) ?? -1) >= 0)
    const raw = answered.reduce((sum, q) => sum + ((answers[q.id] as number) ?? 0), 0)

    let scaled: number
    if (answered.length === 0) {
      scaled = -1
    } else if (answered.length === dimQuestions.length) {
      scaled = data.scaledScores[raw] ?? 0
    } else {
      const avgPerQuestion = raw / answered.length
      const extrapolatedRaw = Math.round(avgPerQuestion * dimQuestions.length)
      scaled = data.scaledScores[extrapolatedRaw] ?? 0
    }

    const effectiveScaled = Math.max(scaled, 0)
    const threshold =
      data.scoreThresholds.find((t) => effectiveScaled >= t.min && effectiveScaled <= t.max) ||
      data.scoreThresholds[0]
    const description =
      scaled === -1 ? '' : (data.descriptions[dim.id]?.[threshold.label as keyof Description] ?? '')

    return {
      id: dim.id,
      label: dim.label,
      scaled: effectiveScaled,
      scoreLabel: threshold.label,
      displayLabel: scaled === -1 ? 'Skipped' : threshold.displayLabel,
      color: scaled === -1 ? '#94a3b8' : threshold.color,
      description,
    }
  })

  const scored = dimensions.filter((d) => d.displayLabel !== 'Skipped')
  const overall =
    scored.length > 0 ? Math.round(scored.reduce((s, d) => s + d.scaled, 0) / scored.length) : 0

  const overallThreshold =
    data.scoreThresholds.find((t) => overall >= t.min && overall <= t.max) ||
    data.scoreThresholds[0]

  return {
    dimensions,
    overall,
    overallDisplayLabel: overallThreshold.displayLabel,
    overallColor: overallThreshold.color,
  }
}

// ---------------------------------------------------------------------------
// Form submission
// ---------------------------------------------------------------------------

async function submitScorecard() {
  const form = document.getElementById('gate-form') as HTMLFormElement
  const submitBtn = form.querySelector('button[type="submit"]') as HTMLButtonElement
  const errorEl = document.getElementById('gate-error')

  // Collect form data
  const firstName = (form.querySelector('[name="first_name"]') as HTMLInputElement)?.value?.trim()
  const email = (form.querySelector('[name="email"]') as HTMLInputElement)?.value?.trim()
  const businessName = (
    form.querySelector('[name="business_name"]') as HTMLInputElement
  )?.value?.trim()
  const phone = (form.querySelector('[name="phone"]') as HTMLInputElement)?.value?.trim()
  const honeypot = (form.querySelector('[name="website_url"]') as HTMLInputElement)?.value

  if (!firstName || !email || !businessName) {
    if (errorEl) {
      errorEl.textContent = 'Please fill in all required fields.'
      errorEl.classList.remove('hidden')
    }
    return
  }

  // Disable submit
  submitBtn.disabled = true
  submitBtn.textContent = 'Loading...'

  // Compute scores client-side and render results immediately
  const scores = computeClientScores()
  renderResults(scores)
  goToStep(23)

  // Build scored answers (only q1-q18, numeric values)
  const scoredAnswers: Record<string, number> = {}
  for (let i = 1; i <= 18; i++) {
    scoredAnswers[`q${i}`] = (answers[`q${i}`] as number) ?? 0
  }

  // Fire-and-forget POST to API
  try {
    await fetch('/api/scorecard/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        vertical: answers.vertical || 'other',
        employee_range: answers.employee_range || '11-25',
        role: answers.role || 'owner',
        answers: scoredAnswers,
        first_name: firstName,
        email,
        business_name: businessName,
        phone: phone || undefined,
        website_url: honeypot || '',
        started_at: startedAt,
      }),
    })
  } catch {
    // API failure doesn't affect the user experience — results already showing
    console.error('[scorecard] Submit failed')
  }
}

// ---------------------------------------------------------------------------
// Results rendering
// ---------------------------------------------------------------------------

function renderResults(scores: {
  dimensions: DimensionResult[]
  overall: number
  overallDisplayLabel: string
  overallColor: string
}) {
  const container = document.getElementById('results-content')
  if (!container) return

  const sorted = [...scores.dimensions].sort((a, b) => a.scaled - b.scaled)
  const topProblems = sorted.filter((d) => d.scoreLabel !== 'strong').slice(0, 3)

  container.innerHTML = `
    <!-- Score Header -->
    <div class="py-12 text-center">
      <p class="text-sm font-medium uppercase tracking-wider text-slate-500">Your Operations Health Score</p>
      <p class="mt-2 text-7xl font-extrabold" style="color: ${scores.overallColor}">${scores.overall}</p>
      <span class="mt-3 inline-block rounded-full px-4 py-1 text-sm font-semibold" style="background-color: ${scores.overallColor}20; color: ${scores.overallColor}">
        ${scores.overallDisplayLabel}
      </span>
      <div class="mx-auto mt-8 max-w-lg border-t border-slate-200"></div>
    </div>

    <!-- Dimension Breakdown -->
    <div class="mx-auto max-w-2xl px-6 py-12">
      <h3 class="text-xl font-bold text-slate-900">How you scored across 6 areas</h3>
      <div class="mt-8 space-y-5">
        ${sorted
          .map(
            (d) => `
          <div class="flex items-center gap-4">
            <span class="w-40 text-sm font-medium text-slate-700">${d.label}</span>
            <div class="relative h-3 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div class="absolute inset-y-0 left-0 rounded-full" style="width: ${Math.max(d.scaled, 3)}%; background-color: ${d.color}"></div>
            </div>
            <span class="w-8 text-right text-sm font-semibold text-slate-700">${d.scaled}</span>
            ${d.scoreLabel !== 'strong' && d.scoreLabel !== 'getting_there' ? '<span class="material-symbols-outlined text-amber-500 text-base">warning</span>' : '<span class="w-6"></span>'}
          </div>`
          )
          .join('')}
      </div>
    </div>

    <!-- Opportunities -->
    ${
      topProblems.length > 0
        ? `
    <div class="bg-slate-50 px-6 py-12">
      <div class="mx-auto max-w-2xl">
        <h3 class="text-xl font-bold text-slate-900">Where we'd start</h3>
        <p class="mt-2 text-base text-slate-600">Based on your answers, the areas with the most room for improvement are:</p>
        <div class="mt-6 space-y-4">
          ${topProblems
            .map(
              (d) => `
            <div class="rounded-lg border border-slate-200 bg-white p-6">
              <p class="font-semibold text-slate-900">${d.label}</p>
              <p class="mt-1 text-sm leading-relaxed text-slate-600">${d.description}</p>
            </div>`
            )
            .join('')}
        </div>
      </div>
    </div>`
        : ''
    }

    <!-- CTA -->
    <div class="px-6 py-16 text-center">
      <h3 class="text-xl font-bold text-slate-900">Want to dig deeper?</h3>
      <p class="mx-auto mt-2 max-w-lg text-base leading-relaxed text-slate-600">
        This scorecard gives you the lay of the land. The real value comes from a conversation — walking through your day together and figuring out exactly what to fix first.
      </p>
      <a href="/book" class="mt-6 inline-block rounded-lg bg-primary px-8 py-3 text-base font-semibold text-white transition-opacity hover:opacity-80">
        Book an assessment call
      </a>
      <p class="mt-3 text-sm text-slate-400">Full report sent to your email</p>
      <p class="mt-2 text-sm text-slate-500">
        Not ready to schedule?
        <a href="/get-started" class="font-medium text-primary underline hover:opacity-80">Tell us about your business</a>
        and we'll follow up.
      </p>
    </div>
  `
}
