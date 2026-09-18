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
import { useMemo } from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { Menu } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { type TopNavLink } from '../types'

type TopNavProps = React.HTMLAttributes<HTMLElement> & {
  links: TopNavLink[]
}

function isPathActive(pathname: string, href: string) {
  if (href === '/') {
    return pathname === '/'
  }

  return (
    pathname === href ||
    pathname === `${href}/` ||
    pathname.startsWith(`${href}/`)
  )
}

export function TopNav({ className, links, ...props }: TopNavProps) {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const normalizedLinks = useMemo(
    () =>
      links.map((link) => ({
        ...link,
        isActive:
          link.isActive ??
          (!link.external && isPathActive(pathname, link.href)),
        disabled: link.disabled ?? false,
        external: link.external ?? false,
        reload: link.reload ?? false,
      })),
    [links, pathname]
  )

  return (
    <>
      <div className='lg:hidden'>
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger
            render={<Button size='icon' variant='outline' className='size-7' />}
          >
            <Menu />
          </DropdownMenuTrigger>
          <DropdownMenuContent side='bottom' align='start'>
            {normalizedLinks.map(
              ({ title, href, isActive, disabled, external, reload }) => (
                <DropdownMenuItem
                  key={`${title}-${href}`}
                  className={cn(disabled && 'pointer-events-none opacity-50')}
                  render={
                    external ? (
                      <a
                        href={href}
                        target='_blank'
                        rel='noopener noreferrer'
                        className={!isActive ? 'text-muted-foreground' : ''}
                      >
                        {title}
                      </a>
                    ) : reload ? (
                      <a
                        href={href}
                        className={!isActive ? 'text-muted-foreground' : ''}
                        aria-disabled={disabled}
                      >
                        {title}
                      </a>
                    ) : (
                      <Link
                        to={href}
                        className={!isActive ? 'text-muted-foreground' : ''}
                        disabled={disabled}
                      >
                        {title}
                      </Link>
                    )
                  }
                ></DropdownMenuItem>
              )
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <nav
        className={cn('hidden items-center gap-5 lg:flex xl:gap-7', className)}
        {...props}
      >
        {normalizedLinks.map(({ title, href, isActive, disabled, external, reload }) =>
          external ? (
            <a
              key={`${title}-${href}`}
              href={href}
              target='_blank'
              rel='noopener noreferrer'
              className={cn(
                'hover:text-primary text-[14px] leading-none font-medium whitespace-nowrap transition-colors',
                isActive ? 'text-foreground' : 'text-muted-foreground',
                disabled && 'pointer-events-none opacity-50'
              )}
            >
              {title}
            </a>
          ) : reload ? (
            <a
              key={`${title}-${href}`}
              href={href}
              aria-disabled={disabled}
              className={cn(
                'hover:text-primary text-[14px] leading-none font-medium whitespace-nowrap transition-colors',
                isActive ? 'text-foreground' : 'text-muted-foreground',
                disabled && 'pointer-events-none opacity-50'
              )}
            >
              {title}
            </a>
          ) : (
            <Link
              key={`${title}-${href}`}
              to={href}
              disabled={disabled}
              className={cn(
                'hover:text-primary text-[14px] leading-none font-medium whitespace-nowrap transition-colors',
                isActive ? 'text-foreground' : 'text-muted-foreground',
                disabled && 'pointer-events-none opacity-50'
              )}
            >
              {title}
            </Link>
          )
        )}
      </nav>
    </>
  )
}
