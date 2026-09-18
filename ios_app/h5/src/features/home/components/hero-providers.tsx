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
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

const capabilityRows = [
  [
    'Chat Completions',
    'Responses',
    'Images',
    'Videos',
    'Embeddings',
    'Audio',
    'Rerank',
    'Realtime',
  ],
  [
    'Model Aggregation',
    'Unified Auth',
    'Usage Billing',
    'Token Management',
    'Channel Routing',
    'Failover',
    'Load Balancing',
    'Rate Limits',
  ],
  [
    'API Gateway',
    'AI Assets',
    'Developer Console',
    'Usage Logs',
    'Billing',
    'Multimodal',
    'Claude Compatible',
    'Gemini Compatible',
  ],
] as const

function CapabilityMarquee({
  items,
  reverse,
  speed,
}: {
  items: readonly string[]
  reverse?: boolean
  speed: 'slow' | 'normal' | 'fast'
}) {
  const { t } = useTranslation()
  const loopItems = [...items, ...items]

  return (
    <div className='hero-capability-row' aria-hidden='true'>
      <div
        className={cn(
          'hero-capability-track',
          reverse && 'hero-capability-track-reverse',
          speed === 'slow' && 'hero-capability-track-slow',
          speed === 'fast' && 'hero-capability-track-fast'
        )}
      >
        {loopItems.map((item, index) => (
          <span className='hero-capability-chip' key={`${item}-${index}`}>
            {t(item)}
          </span>
        ))}
      </div>
    </div>
  )
}

export function HeroProviders() {
  return (
    <div className='hero-capability-wall'>
      <CapabilityMarquee items={capabilityRows[0]} speed='normal' />
      <CapabilityMarquee
        items={capabilityRows[1]}
        reverse
        speed='slow'
      />
      <CapabilityMarquee items={capabilityRows[2]} speed='fast' />
    </div>
  )
}
