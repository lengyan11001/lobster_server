/*
Copyright (C) 2023-2026 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/

type ModuleAccess = { enabled: boolean; requireAuth: boolean }

const DEFAULTS: Record<string, ModuleAccess> = {
  pricing: { enabled: true, requireAuth: false },
  rankings: { enabled: true, requireAuth: false },
}

const SIMPLE_DEFAULTS = {
  home: true,
  workbench: true,
  canvas: true,
  console: true,
  docs: true,
  about: true,
  promotion: true,
}

type SimpleHeaderModule = keyof typeof SIMPLE_DEFAULTS

function parseAccess(raw: unknown, fallback: ModuleAccess): ModuleAccess {
  if (typeof raw === 'boolean')
    return { enabled: raw, requireAuth: fallback.requireAuth }
  if (raw && typeof raw === 'object') {
    const r = raw as Record<string, unknown>
    return {
      enabled: typeof r.enabled === 'boolean' ? r.enabled : fallback.enabled,
      requireAuth:
        typeof r.requireAuth === 'boolean'
          ? r.requireAuth
          : fallback.requireAuth,
    }
  }
  return { ...fallback }
}

function getCachedStatus(): Record<string, unknown> | null {
  try {
    const raw = window.localStorage.getItem('status')
    return raw ? (JSON.parse(raw) as Record<string, unknown>) : null
  } catch {
    return null
  }
}

export function getModuleAccess(module: 'rankings' | 'pricing'): ModuleAccess {
  const status = getCachedStatus()
  if (!status) return DEFAULTS[module]

  const rawNav = status.HeaderNavModules
  if (!rawNav || String(rawNav).trim() === '') return DEFAULTS[module]

  try {
    const parsed = JSON.parse(String(rawNav)) as Record<string, unknown>
    return parseAccess(parsed[module], DEFAULTS[module])
  } catch {
    return DEFAULTS[module]
  }
}

export function isHeaderModuleEnabledFromValue(
  rawNav: unknown,
  module: SimpleHeaderModule
): boolean {
  const fallback = SIMPLE_DEFAULTS[module]
  if (!rawNav || String(rawNav).trim() === '') return fallback

  try {
    const parsed = JSON.parse(String(rawNav)) as Record<string, unknown>
    const raw = parsed[module]
    if (typeof raw === 'boolean') return raw
    if (typeof raw === 'string') {
      const normalized = raw.trim().toLowerCase()
      if (normalized === 'true' || normalized === '1') return true
      if (normalized === 'false' || normalized === '0') return false
    }
    if (typeof raw === 'number') return raw === 1
    return fallback
  } catch {
    return fallback
  }
}

export function isHeaderModuleEnabled(module: SimpleHeaderModule): boolean {
  const status = getCachedStatus()
  return isHeaderModuleEnabledFromValue(status?.HeaderNavModules, module)
}

export function isSidebarModuleEnabled(
  section: string,
  module: string
): boolean {
  const status = getCachedStatus()
  return isSidebarModuleEnabledFromValue(
    status?.SidebarModulesAdmin,
    section,
    module
  )
}

export function isSidebarModuleEnabledFromValue(
  raw: unknown,
  section: string,
  module: string
): boolean {
  if (!raw || String(raw).trim() === '') return true

  try {
    const parsed = JSON.parse(String(raw)) as Record<
      string,
      Record<string, boolean>
    >
    const sectionConfig = parsed[section]
    if (!sectionConfig) return true
    if (sectionConfig.enabled === false) return false
    if (sectionConfig[module] === false) return false
    return true
  } catch {
    return true
  }
}
