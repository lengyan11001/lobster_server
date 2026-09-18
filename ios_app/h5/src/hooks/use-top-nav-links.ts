import { useTranslation } from 'react-i18next'
import { EXTERNAL_API_DOCS_URL } from '@/lib/constants'
import {
  getModuleAccess,
  isHeaderModuleEnabled,
  isHeaderModuleEnabledFromValue,
  isSidebarModuleEnabledFromValue,
} from '@/lib/nav-modules'
import { useStatus } from '@/hooks/use-status'

export type TopNavLink = {
  title: string
  href: string
  disabled?: boolean
  external?: boolean
  reload?: boolean
}

export function useTopNavLinks(): TopNavLink[] {
  const { t } = useTranslation()
  const { status } = useStatus()
  const pricingAccess = getModuleAccess('pricing')
  const canvasEnabled =
    isHeaderModuleEnabledFromValue(status?.HeaderNavModules, 'canvas') &&
    isSidebarModuleEnabledFromValue(
      status?.SidebarModulesAdmin,
      'chat',
      'canvas'
    )

  const links: Array<TopNavLink | null> = [
    isHeaderModuleEnabled('home') ? { title: t('Home'), href: '/' } : null,
    isHeaderModuleEnabled('workbench')
      ? { title: t('AI Workbench'), href: '/workbench/chat', reload: true }
      : null,
    canvasEnabled ? { title: t('Infinite Canvas'), href: '/canvas' } : null,
    pricingAccess.enabled
      ? { title: t('Model Square'), href: '/pricing' }
      : null,
    isHeaderModuleEnabled('docs')
      ? {
          title: t('API Docs'),
          href: EXTERNAL_API_DOCS_URL,
          external: true,
        }
      : null,
    isHeaderModuleEnabled('about')
      ? { title: t('Usage guide'), href: '/help' }
      : null,
    isHeaderModuleEnabled('promotion')
      ? { title: t('Become a token merchant'), href: '/promotion' }
      : null,
  ]

  return links.filter((link): link is TopNavLink => Boolean(link))
}
