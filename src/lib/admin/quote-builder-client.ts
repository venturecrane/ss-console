import { parseLineItems } from '../db/quote-content'

type QuoteLineItem = {
  problem: string
  description: string
  estimated_hours: number
}

function formatDollars(amount: number): string {
  return (
    '$' +
    amount.toLocaleString('en-US', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    })
  )
}

interface QuoteBuilderElements {
  root: HTMLElement
  lineItemsBody: HTMLElement
  rate: number
  isDraft: boolean
  addBtn: HTMLElement | null
  warning: HTMLElement | null
  totalHoursEl: HTMLElement | null
  totalPriceEl: HTMLElement | null
  depositAmountEl: HTMLElement | null
  completionAmountEl: HTMLElement | null
  depositSelect: HTMLSelectElement | null
  saveLineItemsInput: HTMLInputElement | null
  saveDepositPctInput: HTMLInputElement | null
  saveScheduleInput: HTMLInputElement | null
  saveDeliverablesInput: HTMLInputElement | null
  saveEngagementOverviewInput: HTMLInputElement | null
  saveMilestoneLabelInput: HTMLInputElement | null
  saveOriginatingSignalInput: HTMLInputElement | null
  scheduleRowsContainer: HTMLElement | null
  deliverableRowsContainer: HTMLElement | null
  addScheduleRowBtn: HTMLElement | null
  addDeliverableRowBtn: HTMLElement | null
  engagementOverviewInput: HTMLTextAreaElement | null
  milestoneLabelInput: HTMLInputElement | null
  originatingSignalInput: HTMLSelectElement | null
  generatePdfBtn: HTMLButtonElement | null
  signBtn: HTMLButtonElement | null
  saveForm: HTMLElement | null
  generatePdfForm: HTMLElement | null
  signForm: HTMLElement | null
}

function inputVal(row: Element, sel: string): string {
  const el = row.querySelector(sel)
  return el instanceof HTMLInputElement ? el.value : ''
}

function textareaVal(row: Element, sel: string): string {
  const el = row.querySelector(sel)
  return el instanceof HTMLTextAreaElement ? el.value : ''
}

function getLineItems(lineItemsBody: HTMLElement): QuoteLineItem[] {
  const rows = lineItemsBody.querySelectorAll('.line-item-row')
  const items: QuoteLineItem[] = []
  rows.forEach((row) => {
    const problem = inputVal(row, '.item-problem')
    const description = inputVal(row, '.item-description')
    const hours = parseFloat(inputVal(row, '.item-hours') || '0')
    if (problem.trim() || description.trim()) {
      items.push({
        problem: problem.trim(),
        description: description.trim(),
        estimated_hours: Number.isNaN(hours) ? 0 : hours,
      })
    }
  })
  return items
}

function clearEmptyState(container: HTMLElement | null): void {
  if (!container) return
  const empty = container.querySelector('.schedule-empty-state, .deliverable-empty-state')
  if (empty) empty.remove()
}

function maybeShowEmptyState(container: HTMLElement | null, isDeliverable: boolean): void {
  if (!container) return
  const rows = container.querySelectorAll(isDeliverable ? '.deliverable-row' : '.schedule-row')
  if (rows.length > 0) return
  const div = document.createElement('div')
  div.className =
    'text-sm text-[color:var(--ss-color-text-secondary)] italic py-2 ' +
    (isDeliverable ? 'deliverable-empty-state' : 'schedule-empty-state')
  div.textContent = container.getAttribute('data-empty-text') ?? ''
  container.appendChild(div)
}

function getScheduleRows(container: HTMLElement | null): Array<{ label: string; body: string }> {
  if (!container) return []
  const rows = container.querySelectorAll('.schedule-row')
  const out: Array<{ label: string; body: string }> = []
  rows.forEach((row) => {
    const label = inputVal(row, '.schedule-label').trim()
    const bodyText = textareaVal(row, '.schedule-body').trim()
    if (label.length > 0 || bodyText.length > 0) out.push({ label, body: bodyText })
  })
  return out
}

function getDeliverableRows(container: HTMLElement | null): Array<{ title: string; body: string }> {
  if (!container) return []
  const rows = container.querySelectorAll('.deliverable-row')
  const out: Array<{ title: string; body: string }> = []
  rows.forEach((row) => {
    const title = inputVal(row, '.deliverable-title').trim()
    const bodyText = textareaVal(row, '.deliverable-body').trim()
    if (title.length > 0 || bodyText.length > 0) out.push({ title, body: bodyText })
  })
  return out
}

function syncAuthoredContentInputs(els: QuoteBuilderElements): void {
  if (els.saveScheduleInput)
    els.saveScheduleInput.value = JSON.stringify(getScheduleRows(els.scheduleRowsContainer))
  if (els.saveDeliverablesInput)
    els.saveDeliverablesInput.value = JSON.stringify(
      getDeliverableRows(els.deliverableRowsContainer)
    )
  if (els.saveEngagementOverviewInput && els.engagementOverviewInput) {
    els.saveEngagementOverviewInput.value = els.engagementOverviewInput.value
  }
  if (els.saveMilestoneLabelInput && els.milestoneLabelInput) {
    els.saveMilestoneLabelInput.value = els.milestoneLabelInput.value
  }
  if (els.saveOriginatingSignalInput && els.originatingSignalInput) {
    els.saveOriginatingSignalInput.value = els.originatingSignalInput.value
  }
}

function renumberRows(lineItemsBody: HTMLElement): void {
  const rows = lineItemsBody.querySelectorAll('.line-item-row')
  rows.forEach((row, i) => {
    const num = row.querySelector('.row-number')
    if (num) num.textContent = String(i + 1)
    row.setAttribute('data-index', String(i))
  })
}

function recalculate(els: QuoteBuilderElements): void {
  const items = getLineItems(els.lineItemsBody)
  const totalHours = items.reduce((sum, item) => sum + item.estimated_hours, 0)
  const totalPrice = totalHours * els.rate
  const depositPct = parseFloat(els.depositSelect?.value ?? '0.5')

  if (els.totalHoursEl) els.totalHoursEl.textContent = String(totalHours)
  if (els.totalPriceEl) els.totalPriceEl.textContent = formatDollars(totalPrice)

  const depositAmount = totalPrice * depositPct
  const completionAmount = totalPrice * (1 - depositPct)
  if (els.depositAmountEl) els.depositAmountEl.textContent = formatDollars(depositAmount)
  if (els.completionAmountEl) els.completionAmountEl.textContent = formatDollars(completionAmount)
  if (els.warning) els.warning.classList.toggle('hidden', items.length < 3)
  if (els.saveLineItemsInput) els.saveLineItemsInput.value = JSON.stringify(items)
  if (els.saveDepositPctInput) els.saveDepositPctInput.value = String(depositPct)
  renumberRows(els.lineItemsBody)
}

function addScheduleRow(els: QuoteBuilderElements): void {
  if (!els.scheduleRowsContainer) return
  clearEmptyState(els.scheduleRowsContainer)
  const div = document.createElement('div')
  div.className = 'flex gap-2 mb-2 schedule-row'
  div.innerHTML = `
    <input type="text" class="w-32 border border-[color:var(--ss-color-border)] rounded px-2 py-1 text-sm schedule-label" placeholder="Label" />
    <input type="text" class="flex-1 border border-[color:var(--ss-color-border)] rounded px-2 py-1 text-sm schedule-body" placeholder="What happens in this phase" />
    <button type="button" class="text-[color:var(--ss-color-text-muted)] hover:text-error transition-colors remove-schedule-row-btn px-2" title="Remove">&times;</button>
  `
  els.scheduleRowsContainer.appendChild(div)
}

function addDeliverableRow(els: QuoteBuilderElements): void {
  if (!els.deliverableRowsContainer) return
  clearEmptyState(els.deliverableRowsContainer)
  const div = document.createElement('div')
  div.className = 'mb-3 deliverable-row'
  div.innerHTML = `
    <div class="flex gap-2">
      <div class="flex-1 space-y-1">
        <input type="text" class="w-full border border-[color:var(--ss-color-border)] rounded px-2 py-1 text-sm deliverable-title" placeholder="Title" />
        <textarea class="w-full border border-[color:var(--ss-color-border)] rounded px-2 py-1 text-sm deliverable-body" rows="2" placeholder="Description"></textarea>
      </div>
      <button type="button" class="text-[color:var(--ss-color-text-muted)] hover:text-error transition-colors remove-deliverable-row-btn px-2" title="Remove">&times;</button>
    </div>
  `
  els.deliverableRowsContainer.appendChild(div)
}

function addLineItemRow(els: QuoteBuilderElements): void {
  const index = els.lineItemsBody.querySelectorAll('.line-item-row').length
  const tr = document.createElement('tr')
  tr.className = 'border-b border-[color:var(--ss-color-border-subtle)] line-item-row'
  tr.setAttribute('data-index', String(index))
  tr.innerHTML = `
    <td class="py-2 px-2 text-[color:var(--ss-color-text-muted)] row-number">${index + 1}</td>
    <td class="py-2 px-2">
      <input type="text" class="w-full border border-[color:var(--ss-color-border)] rounded px-2 py-1 text-sm item-problem" value="" placeholder="e.g., Scheduling chaos" />
    </td>
    <td class="py-2 px-2">
      <input type="text" class="w-full border border-[color:var(--ss-color-border)] rounded px-2 py-1 text-sm item-description" value="" placeholder="What we'll deliver" />
    </td>
    <td class="py-2 px-2 text-right">
      <input type="number" class="w-20 border border-[color:var(--ss-color-border)] rounded px-2 py-1 text-sm text-right item-hours" value="0" min="0.5" step="0.5" />
    </td>
    <td class="py-2 px-2 text-center">
      <button type="button" class="text-[color:var(--ss-color-text-muted)] hover:text-error transition-colors remove-item-btn" title="Remove">&times;</button>
    </td>
  `
  els.lineItemsBody.appendChild(tr)
  recalculate(els)
}

function wireEventListeners(els: QuoteBuilderElements, initialLineItems: QuoteLineItem[]): void {
  if (els.addScheduleRowBtn)
    els.addScheduleRowBtn.addEventListener('click', () => addScheduleRow(els))
  if (els.addDeliverableRowBtn)
    els.addDeliverableRowBtn.addEventListener('click', () => addDeliverableRow(els))
  if (els.addBtn) els.addBtn.addEventListener('click', () => addLineItemRow(els))

  if (els.scheduleRowsContainer) {
    els.scheduleRowsContainer.addEventListener('click', (e) => {
      const target = e.target as HTMLElement | null
      if (!target?.classList.contains('remove-schedule-row-btn')) return
      target.closest('.schedule-row')?.remove()
      maybeShowEmptyState(els.scheduleRowsContainer, false)
    })
  }

  if (els.deliverableRowsContainer) {
    els.deliverableRowsContainer.addEventListener('click', (e) => {
      const target = e.target as HTMLElement | null
      if (!target?.classList.contains('remove-deliverable-row-btn')) return
      target.closest('.deliverable-row')?.remove()
      maybeShowEmptyState(els.deliverableRowsContainer, true)
    })
  }

  els.lineItemsBody.addEventListener('click', (e) => {
    const target = e.target as HTMLElement | null
    if (!target?.classList.contains('remove-item-btn')) return
    const row = target.closest('.line-item-row')
    if (row && els.lineItemsBody.querySelectorAll('.line-item-row').length > 1) {
      row.remove()
      recalculate(els)
    }
  })

  els.lineItemsBody.addEventListener('input', () => recalculate(els))
  if (els.depositSelect) els.depositSelect.addEventListener('change', () => recalculate(els))

  if (els.saveForm) {
    els.saveForm.addEventListener('submit', () => {
      recalculate(els)
      syncAuthoredContentInputs(els)
    })
  }
  if (els.generatePdfForm && els.generatePdfBtn) {
    els.generatePdfForm.addEventListener('submit', () => {
      els.generatePdfBtn!.disabled = true
      els.generatePdfBtn!.textContent = 'Generating...'
    })
  }
  if (els.signForm && els.signBtn) {
    els.signForm.addEventListener('submit', () => {
      els.signBtn!.disabled = true
      els.signBtn!.textContent = 'Sending...'
    })
  }
  if (els.warning && initialLineItems.length >= 3) {
    els.warning.classList.remove('hidden')
  }
}

export function initQuoteBuilderPage(rootId = 'quote-builder-page'): void {
  const root = document.getElementById(rootId)
  if (!(root instanceof HTMLElement)) return

  const isDraft = root.dataset.isDraft === 'true'
  if (!isDraft) return

  const body = document.getElementById('line-items-body')
  if (!(body instanceof HTMLElement)) return

  const rate = Number(root.dataset.rate ?? '0')
  const initialLineItems: QuoteLineItem[] = parseLineItems(root.dataset.lineItems ?? null)

  const els: QuoteBuilderElements = {
    root,
    lineItemsBody: body,
    rate,
    isDraft,
    addBtn: document.getElementById('add-line-item-btn'),
    warning: document.getElementById('line-item-warning'),
    totalHoursEl: document.getElementById('total-hours'),
    totalPriceEl: document.getElementById('total-price'),
    depositAmountEl: document.getElementById('deposit-amount'),
    completionAmountEl: document.getElementById('completion-amount'),
    depositSelect: document.getElementById('deposit-pct-select') as HTMLSelectElement | null,
    saveLineItemsInput: document.getElementById('save-line-items') as HTMLInputElement | null,
    saveDepositPctInput: document.getElementById('save-deposit-pct') as HTMLInputElement | null,
    saveScheduleInput: document.getElementById('save-schedule') as HTMLInputElement | null,
    saveDeliverablesInput: document.getElementById('save-deliverables') as HTMLInputElement | null,
    saveEngagementOverviewInput: document.getElementById(
      'save-engagement-overview'
    ) as HTMLInputElement | null,
    saveMilestoneLabelInput: document.getElementById(
      'save-milestone-label'
    ) as HTMLInputElement | null,
    saveOriginatingSignalInput: document.getElementById(
      'save-originating-signal-id'
    ) as HTMLInputElement | null,
    scheduleRowsContainer: document.getElementById('schedule-rows'),
    deliverableRowsContainer: document.getElementById('deliverable-rows'),
    addScheduleRowBtn: document.getElementById('add-schedule-row-btn'),
    addDeliverableRowBtn: document.getElementById('add-deliverable-row-btn'),
    engagementOverviewInput: document.getElementById(
      'engagement-overview-input'
    ) as HTMLTextAreaElement | null,
    milestoneLabelInput: document.getElementById(
      'milestone-label-input'
    ) as HTMLInputElement | null,
    originatingSignalInput: document.getElementById(
      'originating-signal-input'
    ) as HTMLSelectElement | null,
    generatePdfBtn: document.getElementById('generate-pdf-btn') as HTMLButtonElement | null,
    signBtn: document.getElementById('sign-btn') as HTMLButtonElement | null,
    saveForm: document.getElementById('save-form'),
    generatePdfForm: document.getElementById('generate-pdf-form'),
    signForm: document.getElementById('sign-form'),
  }

  wireEventListeners(els, initialLineItems)
}
