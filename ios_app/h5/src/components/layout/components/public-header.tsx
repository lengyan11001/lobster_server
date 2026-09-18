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
import { useState, useEffect } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/auth-store'
import { cn } from '@/lib/utils'
import { useNotifications } from '@/hooks/use-notifications'
import { useSystemConfig } from '@/hooks/use-system-config'
import { useTopNavLinks } from '@/hooks/use-top-nav-links'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { LanguageSwitcher } from '@/components/language-switcher'
import { NotificationButton } from '@/components/notification-button'
import { NotificationDialog } from '@/components/notification-dialog'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { defaultTopNavLinks } from '../config/top-nav.config'
import type { TopNavLink } from '../types'
import { HeaderLogo } from './header-logo'

export interface PublicHeaderProps {
  navLinks?: TopNavLink[]
  mobileLinks?: TopNavLink[]
  navContent?: React.ReactNode
  showThemeSwitch?: boolean
  showLanguageSwitcher?: boolean
  logo?: React.ReactNode
  siteName?: string
  homeUrl?: string
  leftContent?: React.ReactNode
  rightContent?: React.ReactNode
  showNavigation?: boolean
  showAuthButtons?: boolean
  showNotifications?: boolean
  className?: string
}

export function PublicHeader(props: PublicHeaderProps) {
  const {
    navLinks = defaultTopNavLinks,
    showThemeSwitch = true,
    showLanguageSwitcher = true,
    logo: customLogo,
    siteName: customSiteName,
    homeUrl = '/',
    showAuthButtons = true,
    showNotifications = true,
  } = props

  const { t } = useTranslation()
  const [mobileOpen, setMobileOpen] = useState(false)
  const { auth } = useAuthStore()
  const { systemName, logo: systemLogo, loading } = useSystemConfig()
  const dynamicLinks = useTopNavLinks()
  const notifications = useNotifications()
  const routerState = useRouterState()
  const pathname = routerState.location.pathname

  const user = auth.user
  const isAuthenticated = !!user
  const displaySiteName = customSiteName || systemName
  const links = dynamicLinks.length > 0 ? dynamicLinks : navLinks
  const headerLogoSrc =
    systemLogo.includes('/logo.svg') || systemLogo.endsWith('/logo.svg')
      ? '/logo-black.png'
      : systemLogo

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [mobileOpen])

  return (
    <>
      <header className='border-border/40 bg-background/80 sticky top-0 z-50 h-12 w-full border-b backdrop-blur-sm'>
        <nav className='relative mx-auto flex h-full max-w-6xl items-center justify-between px-4 sm:px-6'>
          {/* Logo */}
          <Link
            to={homeUrl}
            className='group flex shrink-0 items-center gap-2.5'
          >
            <div className='flex size-7 shrink-0 items-center justify-center transition-all duration-300 group-hover:scale-105'>
              {customLogo ? (
                customLogo
              ) : (
                <HeaderLogo
                  src={headerLogoSrc}
                  loading={loading}
                  className='size-full rounded-lg'
                />
              )}
            </div>
            <span className='text-[15px] leading-none font-semibold tracking-[-0.02em]'>
              {loading ? <Skeleton className='h-4 w-16' /> : displaySiteName}
            </span>
          </Link>

          {/* Desktop nav */}
          <div className='absolute left-1/2 hidden -translate-x-1/2 items-center gap-0.5 sm:flex'>
            {links.map((link, i) => {
              const isActive =
                link.href === '/'
                  ? pathname === '/'
                  : pathname === link.href ||
                    pathname === `${link.href}/` ||
                    pathname.startsWith(`${link.href}/`)
              if (link.external) {
                return (
                  <a
                    key={i}
                    href={link.href}
                    target='_blank'
                    rel='noopener noreferrer'
                    className={cn(
                      'text-muted-foreground hover:text-foreground rounded-lg px-3 py-1.5 text-[14px] leading-none font-medium whitespace-nowrap transition-colors duration-200',
                      link.disabled && 'pointer-events-none opacity-50'
                    )}
                  >
                    {t(link.title)}
                  </a>
                )
              }
              if (link.reload) {
                return (
                  <a
                    key={i}
                    href={link.href}
                    aria-disabled={link.disabled}
                    className={cn(
                      'rounded-lg px-3 py-1.5 text-[14px] leading-none font-medium whitespace-nowrap transition-colors duration-200',
                      isActive
                        ? 'text-foreground'
                        : 'text-muted-foreground hover:text-foreground',
                      link.disabled && 'pointer-events-none opacity-50'
                    )}
                  >
                    {t(link.title)}
                  </a>
                )
              }
              return (
                <Link
                  key={i}
                  to={link.href}
                  className={cn(
                    'rounded-lg px-3 py-1.5 text-[14px] leading-none font-medium whitespace-nowrap transition-colors duration-200',
                    isActive
                      ? 'text-foreground'
                      : 'text-muted-foreground hover:text-foreground',
                    link.disabled && 'pointer-events-none opacity-50'
                  )}
                >
                  {t(link.title)}
                </Link>
              )
            })}
          </div>

          <div className='ms-auto hidden items-center gap-0.5 sm:flex'>
            {(showLanguageSwitcher || showThemeSwitch || showNotifications) && (
              <div className='bg-border/40 mx-2 h-4 w-px' />
            )}

            {showLanguageSwitcher && <LanguageSwitcher />}
            {showThemeSwitch && <ThemeSwitch />}
            {showNotifications && (
              <NotificationButton
                unreadCount={notifications.unreadCount}
                onClick={() => notifications.openDialog()}
              />
            )}

            {showAuthButtons && (
              <>
                <div className='bg-border/40 mx-1 h-4 w-px' />
                {loading ? (
                  <Skeleton className='h-8 w-20 rounded-lg' />
                ) : isAuthenticated ? (
                  <ProfileDropdown />
                ) : (
                  <Button
                    size='sm'
                    className='h-8 rounded-lg px-3.5 text-[13px] font-medium'
                    render={<Link to='/sign-in' />}
                  >
                    {t('Sign in')}
                  </Button>
                )}
              </>
            )}
          </div>

          {/* Mobile: compact actions + hamburger */}
          <div className='flex items-center gap-2 sm:hidden'>
            {showThemeSwitch && <ThemeSwitch />}
            {showAuthButtons && !loading && isAuthenticated && (
              <ProfileDropdown />
            )}
            <Button
              type='button'
              variant='ghost'
              size='icon'
              className='size-9'
              onClick={() => setMobileOpen((v) => !v)}
              aria-label={t('Toggle navigation menu')}
            >
              <div className='relative size-4'>
                <span
                  className={cn(
                    'absolute inset-x-0 block h-[1.5px] origin-center rounded-full bg-current transition-all duration-300',
                    mobileOpen ? 'top-[7px] rotate-45' : 'top-[3px]'
                  )}
                />
                <span
                  className={cn(
                    'absolute inset-x-0 top-[7px] block h-[1.5px] rounded-full bg-current transition-all duration-300',
                    mobileOpen ? 'scale-x-0 opacity-0' : 'opacity-100'
                  )}
                />
                <span
                  className={cn(
                    'absolute inset-x-0 block h-[1.5px] origin-center rounded-full bg-current transition-all duration-300',
                    mobileOpen ? 'top-[7px] -rotate-45' : 'top-[11px]'
                  )}
                />
              </div>
            </Button>
          </div>
        </nav>
      </header>

      {/* Mobile full-screen overlay */}
      <div
        className={cn(
          'bg-background/98 fixed inset-0 z-40 backdrop-blur-2xl transition-all duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] sm:pointer-events-none sm:hidden',
          mobileOpen
            ? 'pointer-events-auto opacity-100'
            : 'pointer-events-none opacity-0'
        )}
      >
        <div className='flex h-full flex-col justify-between px-8 pt-20 pb-10'>
          <nav className='flex flex-col gap-1'>
            {links.map((link, i) => {
              const isActive = pathname === link.href
              const linkClassName = cn(
                'flex items-center gap-3 py-3 text-base font-medium tracking-tight transition-all duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]',
                mobileOpen
                  ? 'translate-y-0 opacity-100'
                  : 'translate-y-4 opacity-0',
                isActive ? 'text-foreground' : 'text-muted-foreground'
              )

              if (link.external) {
                return (
                  <a
                    key={i}
                    href={link.href}
                    target='_blank'
                    rel='noopener noreferrer'
                    onClick={() => setMobileOpen(false)}
                    className={linkClassName}
                    style={{
                      transitionDelay: mobileOpen ? `${100 + i * 50}ms` : '0ms',
                    }}
                  >
                    {t(link.title)}
                  </a>
                )
              }
              if (link.reload) {
                return (
                  <a
                    key={i}
                    href={link.href}
                    onClick={() => setMobileOpen(false)}
                    className={linkClassName}
                    aria-disabled={link.disabled}
                    style={{
                      transitionDelay: mobileOpen ? `${100 + i * 50}ms` : '0ms',
                    }}
                  >
                    {t(link.title)}
                  </a>
                )
              }

              return (
                <Link
                  key={i}
                  to={link.href}
                  onClick={() => setMobileOpen(false)}
                  className={linkClassName}
                  style={{
                    transitionDelay: mobileOpen ? `${100 + i * 50}ms` : '0ms',
                  }}
                >
                  {t(link.title)}
                </Link>
              )
            })}
          </nav>

          <div
            className={cn(
              'flex flex-col gap-3 transition-all duration-500',
              mobileOpen
                ? 'translate-y-0 opacity-100'
                : 'translate-y-4 opacity-0'
            )}
            style={{ transitionDelay: mobileOpen ? '250ms' : '0ms' }}
          >
            {showAuthButtons && (
              <Link
                to={isAuthenticated ? '/dashboard' : '/sign-in'}
                onClick={() => setMobileOpen(false)}
                className='bg-foreground text-background inline-flex h-10 items-center justify-center rounded-lg text-sm font-medium transition-opacity hover:opacity-90 active:opacity-80'
              >
                {isAuthenticated ? '控制台' : t('Sign in')}
              </Link>
            )}
          </div>
        </div>
      </div>

      {/* Notification Dialog */}
      {showNotifications && (
        <NotificationDialog
          open={notifications.dialogOpen}
          onOpenChange={notifications.setDialogOpen}
          activeTab={notifications.activeTab}
          onTabChange={notifications.setActiveTab}
          notice={notifications.notice}
          announcements={notifications.announcements}
          loading={notifications.loading}
          onCloseToday={notifications.closeToday}
        />
      )}
    </>
  )
}
