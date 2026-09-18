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
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

const banners = [
  {
    image: '/home-banners/gateway-command.png',
    title: 'One API for every AI model',
    subtitle: 'Route requests, manage keys, and observe usage from one gateway.',
  },
  {
    image: '/home-banners/model-router.png',
    title: 'Stable routing across upstreams',
    subtitle: 'Connect providers with failover, limits, and unified billing.',
  },
  {
    image: '/home-banners/asset-cloud.png',
    title: 'Console for teams and assets',
    subtitle: 'Keep models, applications, and digital assets under control.',
  },
] as const

export function HeroAdCarousel() {
  const { t } = useTranslation()
  const [activeIndex, setActiveIndex] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActiveIndex((index) => (index + 1) % banners.length)
    }, 4500)

    return () => window.clearInterval(timer)
  }, [])

  return (
    <section className='hero-ad-carousel' aria-label={t('Homepage banners')}>
      {banners.map((banner, index) => (
        <div
          className={cn(
            'hero-ad-slide',
            index === activeIndex && 'hero-ad-slide-active'
          )}
          key={banner.image}
          aria-hidden={index !== activeIndex}
        >
          <img
            src={banner.image}
            alt=''
            className='hero-ad-image'
            draggable={false}
          />
          <div className='hero-ad-content'>
            <p className='hero-ad-kicker'>{t('OpenMind API')}</p>
            <h1 className='hero-ad-title'>{t(banner.title)}</h1>
            <p className='hero-ad-subtitle'>{t(banner.subtitle)}</p>
          </div>
        </div>
      ))}

      <div className='hero-ad-dots' aria-hidden='true'>
        {banners.map((banner, index) => (
          <span
            className={cn(
              'hero-ad-dot',
              index === activeIndex && 'hero-ad-dot-active'
            )}
            key={banner.image}
          />
        ))}
      </div>
    </section>
  )
}
