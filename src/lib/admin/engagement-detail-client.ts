const PHOTO_TARGET_SIZE = 400
const PHOTO_PREVIEW_SIZE = 160
const PHOTO_MAX_BYTES = 5 * 1024 * 1024

interface PhotoCtx {
  engagementId: string
  input: HTMLInputElement
  uploadBtn: HTMLButtonElement
  cancelBtn: HTMLButtonElement
  previewEl: HTMLCanvasElement
  statusEl: HTMLElement
  removeBtn: HTMLButtonElement | null
  getPending: () => Blob | null
  setPending: (b: Blob | null) => void
}

function setPhotoStatus(statusEl: HTMLElement, msg: string, isError: boolean): void {
  statusEl.textContent = msg
  statusEl.className = isError
    ? 'mt-2 text-xs text-[color:var(--ss-color-error)]'
    : 'mt-2 text-xs text-[color:var(--ss-color-text-secondary)]'
}

async function cropToSquareWebp(file: File, previewEl: HTMLCanvasElement): Promise<Blob> {
  if (file.size > PHOTO_MAX_BYTES)
    throw new Error('Original file exceeds 5 MB. Please pick a smaller image.')
  const bitmap = await createImageBitmap(file)
  const side = Math.min(bitmap.width, bitmap.height)
  const sx = Math.round((bitmap.width - side) / 2)
  const sy = Math.round((bitmap.height - side) / 2)

  const canvas = document.createElement('canvas')
  canvas.width = PHOTO_TARGET_SIZE
  canvas.height = PHOTO_TARGET_SIZE
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Could not initialize image processing.')
  ctx.drawImage(bitmap, sx, sy, side, side, 0, 0, PHOTO_TARGET_SIZE, PHOTO_TARGET_SIZE)

  previewEl.width = PHOTO_PREVIEW_SIZE
  previewEl.height = PHOTO_PREVIEW_SIZE
  const pCtx = previewEl.getContext('2d')
  if (!pCtx) throw new Error('Could not initialize preview canvas.')
  pCtx.drawImage(canvas, 0, 0, PHOTO_PREVIEW_SIZE, PHOTO_PREVIEW_SIZE)
  previewEl.classList.remove('hidden')

  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Encoding failed'))), 'image/webp', 0.9)
  })
  if (blob.size > PHOTO_MAX_BYTES)
    throw new Error('Encoded photo still exceeds 5 MB. Try a smaller source image.')
  return blob
}

async function handlePhotoChange(ctx: PhotoCtx): Promise<void> {
  const file = ctx.input.files?.[0]
  if (!file) return
  try {
    setPhotoStatus(ctx.statusEl, 'Processing image...', false)
    const blob = await cropToSquareWebp(file, ctx.previewEl)
    ctx.setPending(blob)
    setPhotoStatus(
      ctx.statusEl,
      `Ready to upload (${(blob.size / 1024).toFixed(1)} KB WebP)`,
      false
    )
    ctx.uploadBtn.classList.remove('hidden')
    ctx.cancelBtn.classList.remove('hidden')
  } catch (err) {
    ctx.setPending(null)
    setPhotoStatus(
      ctx.statusEl,
      err instanceof Error ? err.message : 'Could not process image',
      true
    )
  }
}

async function handlePhotoUpload(ctx: PhotoCtx): Promise<void> {
  const pending = ctx.getPending()
  if (!pending) return
  ctx.uploadBtn.disabled = true
  ctx.cancelBtn.disabled = true
  setPhotoStatus(ctx.statusEl, 'Uploading...', false)
  const form = new FormData()
  form.append('photo', new File([pending], 'consultant.webp', { type: 'image/webp' }))
  try {
    const res = await fetch(`/api/admin/engagements/${ctx.engagementId}/consultant-photo`, {
      method: 'POST',
      body: form,
    })
    if (!res.ok) {
      const data = (await res.json().catch(() => ({}))) as { message?: string }
      throw new Error(data.message || res.statusText)
    }
    setPhotoStatus(ctx.statusEl, 'Photo uploaded. Reloading...', false)
    window.setTimeout(() => location.reload(), 500)
  } catch (err) {
    setPhotoStatus(ctx.statusEl, err instanceof Error ? err.message : 'Upload failed', true)
    ctx.uploadBtn.disabled = false
    ctx.cancelBtn.disabled = false
  }
}

async function handlePhotoRemove(ctx: PhotoCtx): Promise<void> {
  if (!ctx.removeBtn || !confirm('Remove the current consultant photo?')) return
  ctx.removeBtn.disabled = true
  setPhotoStatus(ctx.statusEl, 'Removing...', false)
  try {
    const res = await fetch(`/api/admin/engagements/${ctx.engagementId}/consultant-photo`, {
      method: 'DELETE',
    })
    if (!res.ok) {
      const data = (await res.json().catch(() => ({}))) as { message?: string }
      throw new Error(data.message || res.statusText)
    }
    setPhotoStatus(ctx.statusEl, 'Photo removed. Reloading...', false)
    window.setTimeout(() => location.reload(), 500)
  } catch (err) {
    setPhotoStatus(ctx.statusEl, err instanceof Error ? err.message : 'Remove failed', true)
    ctx.removeBtn.disabled = false
  }
}

function setupDeliverablesUpload(engagementId: string): void {
  const fileInput = document.getElementById('file-input')
  const uploadBtn = document.getElementById('upload-btn')
  const uploadArea = document.getElementById('upload-area')
  const uploadStatus = document.getElementById('upload-status')

  if (
    !(fileInput instanceof HTMLInputElement) ||
    !(uploadBtn instanceof HTMLButtonElement) ||
    !(uploadArea instanceof HTMLDivElement) ||
    !(uploadStatus instanceof HTMLDivElement)
  )
    return

  const uploadStatusEl = uploadStatus

  uploadBtn.addEventListener('click', () => fileInput.click())
  fileInput.addEventListener('change', () => {
    void uploadFiles(fileInput.files)
  })

  uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault()
    uploadArea.classList.add('drag-active')
  })
  uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('drag-active')
  })
  uploadArea.addEventListener('drop', (e) => {
    e.preventDefault()
    uploadArea.classList.remove('drag-active')
    void uploadFiles(e.dataTransfer?.files ?? null)
  })

  async function uploadFiles(files: FileList | null): Promise<void> {
    if (!files || files.length === 0) return
    uploadStatusEl.classList.remove('hidden')
    for (const file of files) {
      uploadStatusEl.textContent = `Uploading ${file.name}...`
      const form = new FormData()
      form.append('file', file)
      try {
        const res = await fetch(`/api/admin/engagements/${engagementId}/deliverables`, {
          method: 'POST',
          body: form,
        })
        uploadStatusEl.textContent = res.ok
          ? `Uploaded ${file.name}`
          : `Failed: ${((await res.json().catch(() => ({}))) as { message?: string }).message || res.statusText}`
      } catch (err) {
        uploadStatusEl.textContent =
          err instanceof Error ? `Error: ${err.message}` : 'Error: Upload failed'
      }
    }
    window.setTimeout(() => location.reload(), 800)
  }
}

function setupConsultantPhoto(engagementId: string): void {
  const photoInput = document.getElementById('photo-input')
  const photoChoose = document.getElementById('photo-choose')
  const photoUpload = document.getElementById('photo-upload')
  const photoCancel = document.getElementById('photo-cancel')
  const photoRemove = document.getElementById('photo-remove')
  const photoPreview = document.getElementById('photo-preview')
  const photoStatus = document.getElementById('photo-status')

  if (
    !(photoInput instanceof HTMLInputElement) ||
    !(photoChoose instanceof HTMLButtonElement) ||
    !(photoUpload instanceof HTMLButtonElement) ||
    !(photoCancel instanceof HTMLButtonElement) ||
    !(photoPreview instanceof HTMLCanvasElement) ||
    !(photoStatus instanceof HTMLElement)
  )
    return

  let pendingBlob: Blob | null = null
  const ctx: PhotoCtx = {
    engagementId,
    input: photoInput,
    uploadBtn: photoUpload,
    cancelBtn: photoCancel,
    previewEl: photoPreview,
    statusEl: photoStatus,
    removeBtn: photoRemove instanceof HTMLButtonElement ? photoRemove : null,
    getPending: () => pendingBlob,
    setPending: (b) => {
      pendingBlob = b
    },
  }

  photoChoose.addEventListener('click', () => photoInput.click())
  photoInput.addEventListener('change', () => {
    void handlePhotoChange(ctx)
  })
  photoUpload.addEventListener('click', () => {
    void handlePhotoUpload(ctx)
  })
  photoCancel.addEventListener('click', () => {
    ctx.setPending(null)
    photoInput.value = ''
    photoPreview.classList.add('hidden')
    photoUpload.classList.add('hidden')
    photoCancel.classList.add('hidden')
    setPhotoStatus(photoStatus, '', false)
  })
  if (ctx.removeBtn)
    ctx.removeBtn.addEventListener('click', () => {
      void handlePhotoRemove(ctx)
    })
}

export function initEngagementDetailPage(rootId = 'engagement-page'): void {
  const root = document.getElementById(rootId)
  const engagementId = root instanceof HTMLElement ? root.dataset.engagementId : null
  if (!engagementId) return

  setupDeliverablesUpload(engagementId)
  setupConsultantPhoto(engagementId)
}
