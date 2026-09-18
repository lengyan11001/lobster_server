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
import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard'

export function HeroApiDemo() {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const { copyToClipboard } = useCopyToClipboard({ notify: false })
  const apiUrl = 'https://www.openmindapi.com/v1'

  const handleCopy = async () => {
    const success = await copyToClipboard(apiUrl)
    if (!success) return
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className='hero-api-demo mx-auto mt-8 w-full max-w-xl'>
      <div
        className={cn(
          'flex min-h-14 items-center gap-3 rounded-lg border px-4 py-3 sm:px-5 sm:py-3.5',
          'border-border bg-card/90 backdrop-blur-sm',
          'shadow-sm transition-all duration-200',
          'hover:border-foreground/25 hover:bg-card hover:shadow-md'
        )}
      >
        <code className='text-muted-foreground min-w-0 flex-1 truncate font-mono text-[12px] sm:text-[13px]'>
          <span className='text-foreground/80'>{apiUrl}</span>
        </code>
        <button
          onClick={handleCopy}
          className={cn(
            'rounded-md p-2 transition-all duration-200',
            'hover:bg-muted active:scale-95',
            copied && 'bg-green-500/10'
          )}
          aria-label={t('Copy API address')}
          title={t('Copy API address')}
        >
          {copied ? (
            <Check className='size-4 text-green-500' />
          ) : (
            <Copy className='text-muted-foreground hover:text-foreground size-4 transition-colors' />
          )}
        </button>
      </div>
    </div>
  )
}
