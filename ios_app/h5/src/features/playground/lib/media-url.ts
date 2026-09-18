export function getDisplayImageUrl(url: string) {
  const trimmed = url.trim()
  if (
    !trimmed ||
    trimmed.startsWith('/') ||
    trimmed.startsWith('data:') ||
    /^https:\/\//i.test(trimmed)
  ) {
    return trimmed
  }
  if (!/^http:\/\//i.test(trimmed)) return trimmed
  if (typeof window !== 'undefined' && window.location.protocol === 'http:') {
    return trimmed
  }
  return `/api/public/images/proxy?url=${encodeURIComponent(trimmed)}`
}
