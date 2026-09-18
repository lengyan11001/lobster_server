import { Capacitor } from '@capacitor/core'
import { Browser } from '@capacitor/browser'

function normalizeUrl(input: string): string {
  const trimmed = input.trim()
  if (!trimmed) return trimmed
  if (trimmed.startsWith('/')) {
    return new URL(trimmed, window.location.origin).toString()
  }
  return trimmed
}

function isInternalUrl(url: string): boolean {
  try {
    return new URL(url, window.location.href).origin === window.location.origin
  } catch {
    return false
  }
}

async function openNativeUrl(url: string): Promise<void> {
  if (isInternalUrl(url)) {
    window.location.assign(url)
    return
  }

  if (Capacitor.isNativePlatform()) {
    await Browser.open({ url })
    return
  }

  window.open(url, '_blank', 'noopener,noreferrer')
}

export function installWindowOpenBridge(): void {
  if (typeof window === 'undefined') return

  const key = '__openmindWindowOpenBridgeInstalled__'
  if ((window as unknown as Record<string, unknown>)[key]) return
  ;(window as unknown as Record<string, unknown>)[key] = true

  const originalOpen = window.open.bind(window)
  window.open = ((url?: string | URL | null, target?: string, features?: string) => {
    if (!url) {
      return originalOpen(url as never, target, features)
    }

    const href = normalizeUrl(String(url))
    if (!href) {
      return originalOpen(url as never, target, features)
    }

    if (target === '_self' || isInternalUrl(href)) {
      void openNativeUrl(href)
      return null
    }

    void openNativeUrl(href)
    return null
  }) as typeof window.open

  document.addEventListener(
    'click',
    (event) => {
      const anchor = (event.target as HTMLElement | null)?.closest?.(
        'a[target="_blank"]'
      )
      if (!(anchor instanceof HTMLAnchorElement)) return

      const href = anchor.getAttribute('href')
      if (!href) return

      const normalized = normalizeUrl(href)
      if (!normalized || isInternalUrl(normalized)) return

      event.preventDefault()
      void openNativeUrl(normalized)
    },
    true
  )
}
