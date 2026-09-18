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
import {
  AlertCircleIcon,
  DownloadIcon,
  ImagePlusIcon,
  ImageIcon,
  Loader2Icon,
  VideoIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { getDisplayImageUrl } from '../lib'
import type { GenerationResult } from '../types'

interface GenerationResultsProps {
  results: GenerationResult[]
  onUseImage?: (image: {
    url: string
    displayUrl: string
    result: Extract<GenerationResult, { mode: 'image' }>
    index: number
  }) => void
}

function getStatusLabel(status: GenerationResult['status']) {
  switch (status) {
    case 'loading':
      return 'Generating'
    case 'processing':
      return 'Processing'
    case 'complete':
      return 'Completed'
    case 'error':
      return 'Failed'
    default:
      return status
  }
}

export function GenerationResults({
  results,
  onUseImage,
}: GenerationResultsProps) {
  const { t } = useTranslation()

  if (results.length === 0) return null

  return (
    <div className='space-y-3'>
      {results.map((result) => {
        const Icon = result.mode === 'image' ? ImageIcon : VideoIcon
        const isBusy =
          result.status === 'loading' || result.status === 'processing'
        const progress =
          result.mode === 'video' ? Math.max(0, result.progress ?? 0) : 0

        return (
          <div
            key={result.id}
            className='bg-background overflow-hidden rounded-lg border shadow-sm'
          >
            <div className='flex items-start justify-between gap-3 border-b px-4 py-3'>
              <div className='min-w-0 space-y-1'>
                <div className='flex items-center gap-2 text-sm font-medium'>
                  <Icon className='text-muted-foreground size-4' />
                  <span>
                    {result.mode === 'image'
                      ? t('Image generation')
                      : t('Video generation')}
                  </span>
                  <span
                    className={cn(
                      'rounded-md px-1.5 py-0.5 text-[11px]',
                      result.status === 'error'
                        ? 'bg-destructive/10 text-destructive'
                        : 'bg-muted text-muted-foreground'
                    )}
                  >
                    {t(getStatusLabel(result.status))}
                  </span>
                </div>
                <div className='text-muted-foreground line-clamp-2 text-xs'>
                  {result.prompt}
                </div>
              </div>
              <div className='text-muted-foreground shrink-0 text-xs'>
                {result.model}
              </div>
            </div>

            {result.status === 'error' ? (
              <div className='text-destructive flex items-center gap-2 px-4 py-4 text-sm'>
                <AlertCircleIcon className='size-4' />
                {result.error || t('Request error occurred')}
              </div>
            ) : result.mode === 'image' ? (
              <div className='grid gap-3 p-4 sm:grid-cols-2'>
                {result.images.length === 0 && isBusy ? (
                  <div className='bg-muted flex aspect-square items-center justify-center rounded-lg'>
                    <Loader2Icon className='text-muted-foreground size-6 animate-spin' />
                  </div>
                ) : (
                  result.images.map((image, index) => {
                    const displayUrl = getDisplayImageUrl(image.url)
                    return (
                      <div key={`${result.id}-${index}`} className='space-y-2'>
                        <div className='bg-muted aspect-square overflow-hidden rounded-lg border'>
                          <img
                            alt={t('Generated image {{number}}', {
                              number: index + 1,
                            })}
                            className='size-full object-contain'
                            src={displayUrl}
                          />
                        </div>
                        <div className='grid gap-2 sm:grid-cols-2'>
                          <Button
                            className='w-full'
                            render={
                              <a
                                href={displayUrl}
                                download
                                rel='noreferrer'
                                target='_blank'
                              />
                            }
                            size='sm'
                            variant='outline'
                          >
                            <DownloadIcon className='size-4' />
                            {t('Open image')}
                          </Button>
                          {onUseImage && (
                            <Button
                              className='w-full'
                              onClick={() =>
                                onUseImage({
                                  url: image.url,
                                  displayUrl,
                                  result,
                                  index,
                                })
                              }
                              size='sm'
                              type='button'
                              variant='secondary'
                            >
                              <ImagePlusIcon className='size-4' />
                              {t('Use this image')}
                            </Button>
                          )}
                        </div>
                      </div>
                    )
                  })
                )}
              </div>
            ) : (
              <div className='space-y-3 p-4'>
                {isBusy && (
                  <Progress
                    value={progress}
                    className='w-full'
                    aria-label={t('Video progress')}
                  />
                )}
                {result.videoUrl ? (
                  <>
                    <video
                      className='bg-muted aspect-video w-full rounded-lg border object-contain'
                      controls
                      src={result.videoUrl}
                    />
                    <Button
                      className='w-full'
                      render={
                        <a
                          href={result.videoUrl}
                          download={`${result.model || 'video'}-${result.taskId || result.id}.mp4`}
                          rel='noreferrer'
                          target='_blank'
                        />
                      }
                      size='sm'
                      variant='outline'
                    >
                      <DownloadIcon className='size-4' />
                      {t('Download video')}
                    </Button>
                  </>
                ) : (
                  <div className='bg-muted text-muted-foreground flex aspect-video items-center justify-center rounded-lg text-sm'>
                    <Loader2Icon className='mr-2 size-4 animate-spin' />
                    {t('Waiting for video')}
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
