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
import { Link } from '@tanstack/react-router'
import { ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { HeroAdCarousel } from '../hero-ad-carousel'
import { HeroApiDemo } from '../hero-api-demo'

interface HeroProps {
  className?: string
  isAuthenticated?: boolean
}

export function Hero(_props: HeroProps) {
  const { t } = useTranslation()

  return (
    <>
      <HeroAdCarousel />

      <section className='hero-section hero-section-compact'>
        <div className='mx-auto flex max-w-3xl flex-col items-center text-center'>
          <div
            className='landing-animate-fade-up w-full opacity-0'
            style={{ animationDelay: '0ms' }}
          >
            <HeroApiDemo />
          </div>

          <div
            className='landing-animate-fade-up mt-5 flex items-center gap-3 opacity-0'
            style={{ animationDelay: '80ms' }}
          >
            <Button
              className='hero-cta-button'
              render={<Link to='/dashboard' />}
            >
              {t('Console')}
              <ArrowRight className='ml-2 size-4' />
            </Button>
          </div>
        </div>
      </section>
    </>
  )
}
