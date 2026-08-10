export type MediaAsset = {
  asset_id: string
  url: string
  media_type: string
}

export type MediaResultInfo = {
  taskId: string
  status: string
  terminal: boolean
  success: boolean | null
  assets: MediaAsset[]
  error: string
}

const IN_PROGRESS_STATUSES = new Set([
  'pending', 'queued', 'submitted', 'processing', 'generating', 'running',
  '处理中', '生成中', '排队中', '运行中', '上传中', '等待中',
])

const SUCCESS_STATUSES = new Set([
  'success', 'completed', 'done', 'succeeded', 'finished',
  '已完成', '生成成功', '成功', '完成',
])

const FAILURE_STATUSES = new Set([
  'failed', 'failure', 'error', 'cancelled', 'canceled', 'timeout', 'expired',
  '失败', '错误', '取消', '超时',
])

function nestedObjects(value: unknown): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = []
  const seen = new Set<object>()

  const visit = (item: unknown, depth: number) => {
    if (depth > 8 || out.length >= 240 || item === null || item === undefined) return
    if (typeof item === 'string') {
      const text = item.trim()
      if (!(text.startsWith('{') || text.startsWith('['))) return
      try {
        visit(JSON.parse(text), depth + 1)
      } catch {
        return
      }
      return
    }
    if (Array.isArray(item)) {
      item.slice(0, 80).forEach(value => visit(value, depth + 1))
      return
    }
    if (typeof item !== 'object' || seen.has(item)) return
    seen.add(item)
    const row = item as Record<string, unknown>
    out.push(row)
    Object.values(row).forEach(value => visit(value, depth + 1))
  }

  visit(value, 0)
  return out
}

function cleanString(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value).trim() : ''
}

function mediaTypeFromUrl(url: string, fallback = ''): string {
  const explicit = fallback.trim().toLowerCase()
  if (['image', 'video', 'audio', 'document'].includes(explicit)) return explicit
  const path = url.split(/[?#]/)[0].toLowerCase()
  if (/\.(mp4|webm|mov|m4v|avi)$/.test(path)) return 'video'
  if (/\.(mp3|wav|m4a|aac|ogg)$/.test(path)) return 'audio'
  if (/\.(png|jpe?g|webp|gif|bmp)$/.test(path)) return 'image'
  return 'document'
}

function normalizeAsset(value: unknown): MediaAsset | null {
  if (!value || typeof value !== 'object') return null
  const row = value as Record<string, unknown>
  const url = cleanString(
    row.url || row.source_url || row.public_url || row.file_url || row.file_path ||
    row.image_url || row.video_url || row.output_url,
  )
  const assetId = cleanString(row.asset_id || row.assetId)
  if (!url && !assetId) return null
  return {
    asset_id: assetId,
    url,
    media_type: mediaTypeFromUrl(url, cleanString(row.media_type || row.type || row.kind)),
  }
}

function collectAssets(objects: Record<string, unknown>[]): MediaAsset[] {
  const saved: MediaAsset[] = []
  const output: MediaAsset[] = []
  const direct: MediaAsset[] = []
  const add = (target: MediaAsset[], value: unknown) => {
    const values = Array.isArray(value) ? value : [value]
    for (const raw of values) {
      const asset = normalizeAsset(raw)
      if (asset) target.push(asset)
    }
  }

  for (const row of objects) {
    add(saved, row.saved_assets)
    const resultRefs = row.result_refs
    if (resultRefs && typeof resultRefs === 'object') {
      add(saved, (resultRefs as Record<string, unknown>).saved_assets)
    }
    const rawOutput = row.output
    if (rawOutput && typeof rawOutput === 'object') {
      const outputRow = rawOutput as Record<string, unknown>
      add(output, outputRow.images)
      add(output, outputRow.videos)
      add(output, outputRow.image)
      add(output, outputRow.video)
      add(output, outputRow.file)
    }
    if (
      row.asset_id || row.assetId || row.image_url || row.video_url || row.file_url || row.output_url ||
      ((SUCCESS_STATUSES.has(cleanString(row.status).toLowerCase())) && row.url)
    ) {
      add(direct, row)
    }
  }

  const candidates = saved.length ? saved : (output.length ? output : direct)
  const unique: MediaAsset[] = []
  const seen = new Set<string>()
  for (const asset of candidates) {
    const key = asset.asset_id ? `asset:${asset.asset_id}` : `url:${asset.url}`
    if (!asset.url && !asset.asset_id || seen.has(key)) continue
    seen.add(key)
    unique.push(asset)
  }
  return unique.slice(0, 12)
}

export function inspectMediaResult(value: unknown): MediaResultInfo {
  const objects = nestedObjects(value)
  let taskId = ''
  for (const row of objects) {
    taskId = cleanString(row.task_id || row.taskId)
    if (taskId) break
  }

  let status = ''
  for (const row of [...objects].reverse()) {
    const candidate = cleanString(row.status || row.task_status || row.state).toLowerCase()
    if (IN_PROGRESS_STATUSES.has(candidate) || SUCCESS_STATUSES.has(candidate) || FAILURE_STATUSES.has(candidate)) {
      status = candidate
      break
    }
  }

  const assets = collectAssets(objects)
  let success: boolean | null = null
  if (SUCCESS_STATUSES.has(status) || assets.length > 0) success = true
  if (FAILURE_STATUSES.has(status)) success = false
  if (success === null && objects.some(row => row.ok === false || row.success === false)) success = false

  let error = ''
  if (success === false) {
    for (const row of [...objects].reverse()) {
      error = cleanString(row.error_message || row.fail_reason || row.error || row.message || row.detail)
      if (error) break
    }
  }

  const terminal = success !== null || (Boolean(status) && !IN_PROGRESS_STATUSES.has(status))
  return { taskId, status: status || (assets.length ? 'completed' : ''), terminal, success, assets, error }
}
