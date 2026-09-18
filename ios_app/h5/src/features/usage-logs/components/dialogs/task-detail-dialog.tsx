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
  AlertTriangle,
  Check,
  Clock,
  Copy,
  Database,
  ExternalLink,
  FileJson,
  Info,
  ListTree,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { formatTimestampToDate, formatUseTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { StatusBadge } from '@/components/status-badge'
import { taskActionMapper, taskStatusMapper } from '../../lib/mappers'
import type { TaskLog } from '../../types'

type ParsedPayload =
  | {
      ok: true
      value: unknown
      pretty: string
      type: 'array' | 'object' | 'primitive'
    }
  | {
      ok: false
      value: string
      pretty: string
      type: 'text'
    }
  | {
      ok: false
      value: null
      pretty: ''
      type: 'empty'
    }

interface TaskDetailDialogProps {
  log: TaskLog
  isAdmin: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
}

function parsePayload(value: unknown): ParsedPayload {
  if (value == null || value === '') {
    return { ok: false, value: null, pretty: '', type: 'empty' }
  }

  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return {
        ok: true,
        value: parsed,
        pretty: JSON.stringify(parsed, null, 2),
        type: Array.isArray(parsed)
          ? 'array'
          : parsed !== null && typeof parsed === 'object'
            ? 'object'
            : 'primitive',
      }
    } catch {
      return { ok: false, value, pretty: value, type: 'text' }
    }
  }

  return {
    ok: true,
    value,
    pretty: JSON.stringify(value, null, 2),
    type: Array.isArray(value)
      ? 'array'
      : value !== null && typeof value === 'object'
        ? 'object'
        : 'primitive',
  }
}

function getDurationSeconds(log: TaskLog): number | null {
  if (!log.submit_time || !log.finish_time) return null
  const duration = log.finish_time - log.submit_time
  return duration >= 0 ? duration : null
}

function DetailRow(props: {
  label: ReactNode
  value: ReactNode
  mono?: boolean
  muted?: boolean
}) {
  return (
    <div className='grid min-w-0 grid-cols-[5.5rem_minmax(0,1fr)] gap-2 text-sm sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-3'>
      <span className='text-muted-foreground min-w-0 text-xs'>
        {props.label}
      </span>
      <span
        className={cn(
          'max-w-full min-w-0 text-xs break-all sm:break-words',
          props.mono && 'font-mono',
          props.muted && 'text-muted-foreground'
        )}
      >
        {props.value}
      </span>
    </div>
  )
}

function DetailSection(props: {
  icon?: ReactNode
  label: string
  variant?: 'default' | 'danger'
  children: ReactNode
}) {
  const isDanger = props.variant === 'danger'
  return (
    <div className='min-w-0 space-y-1.5'>
      <Label
        className={cn(
          'flex items-center gap-1.5 text-xs font-semibold',
          isDanger && 'text-red-500'
        )}
      >
        {props.icon}
        {props.label}
      </Label>
      <div
        className={cn(
          'min-w-0 space-y-2 overflow-hidden rounded-md border p-2.5 max-sm:p-2',
          isDanger
            ? 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20'
            : 'bg-muted/30'
        )}
      >
        {props.children}
      </div>
    </div>
  )
}

function JsonBlock(props: {
  label: string
  payload: ParsedPayload
  emptyLabel: string
  copiedText: string | null
  onCopy: (value: string) => void
}) {
  const { t } = useTranslation()

  if (props.payload.type === 'empty') {
    return (
      <div className='bg-background/60 rounded border p-2 text-xs text-muted-foreground'>
        {props.emptyLabel}
      </div>
    )
  }

  const isJson = props.payload.ok
  const copyLabel = isJson ? t('Copy JSON') : t('Copy to clipboard')

  return (
    <div className='min-w-0 space-y-1.5'>
      <div className='flex min-w-0 items-center justify-between gap-2'>
        <span className='text-muted-foreground text-xs'>{props.label}</span>
        <Button
          variant='ghost'
          size='xs'
          className='h-6 px-1.5'
          onClick={() => props.onCopy(props.payload.pretty)}
          title={copyLabel}
          aria-label={copyLabel}
        >
          {props.copiedText === props.payload.pretty ? (
            <Check className='size-3 text-green-600' />
          ) : (
            <Copy className='size-3' />
          )}
          <span className='max-sm:hidden'>{copyLabel}</span>
        </Button>
      </div>
      <pre
        className={cn(
          'bg-background/70 max-h-64 min-w-0 overflow-auto rounded border p-2 font-mono text-[11px] leading-relaxed whitespace-pre',
          !isJson && 'whitespace-pre-wrap break-words'
        )}
      >
        {props.payload.pretty}
      </pre>
    </div>
  )
}

function collectUrls(value: unknown): string[] {
  const urls = new Set<string>()

  const visit = (item: unknown) => {
    if (typeof item === 'string') {
      if (/^https?:\/\//i.test(item)) urls.add(item)
      return
    }
    if (Array.isArray(item)) {
      item.forEach(visit)
      return
    }
    if (item && typeof item === 'object') {
      Object.values(item as Record<string, unknown>).forEach(visit)
    }
  }

  visit(value)
  return Array.from(urls)
}

function UrlList(props: { urls: string[] }) {
  const { t } = useTranslation()

  if (props.urls.length === 0) return null

  return (
    <div className='min-w-0 space-y-1.5'>
      <span className='text-muted-foreground text-xs'>{t('Detected URLs')}</span>
      <div className='space-y-1'>
        {props.urls.map((url) => (
          <a
            key={url}
            href={url}
            target='_blank'
            rel='noopener noreferrer'
            className='bg-background/60 hover:bg-muted flex min-w-0 items-center gap-1.5 rounded border px-2 py-1 text-xs transition-colors'
          >
            <ExternalLink className='text-muted-foreground size-3 shrink-0' />
            <span className='min-w-0 truncate'>{url}</span>
          </a>
        ))}
      </div>
    </div>
  )
}

function DataSummary(props: { payload: ParsedPayload }) {
  const { t } = useTranslation()

  if (!props.payload.ok) return null

  const value = props.payload.value
  if (Array.isArray(value)) {
    return (
      <DetailRow
        label={t('Data Type')}
        value={`${t('Array')} · ${value.length} ${t('Items')}`}
        mono
      />
    )
  }

  if (value && typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>)
    return (
      <DetailRow
        label={t('Data Type')}
        value={`${t('Object')} · ${keys.length} ${t('Fields')}`}
        mono
      />
    )
  }

  return <DetailRow label={t('Data Type')} value={t('Primitive')} mono />
}

export function TaskDetailDialog(props: TaskDetailDialogProps) {
  const { t } = useTranslation()
  const { copiedText, copyToClipboard } = useCopyToClipboard({ notify: false })
  const { log } = props
  const dataPayload = parsePayload(log.data)
  const otherPayload = parsePayload(log.other)
  const duration = getDurationSeconds(log)
  const urls = collectUrls([
    dataPayload.ok ? dataPayload.value : dataPayload.value,
    otherPayload.ok ? otherPayload.value : otherPayload.value,
    log.fail_reason,
  ])

  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent className='min-w-0 overflow-hidden max-sm:max-h-[calc(100dvh-1.5rem)] max-sm:w-[calc(100vw-1.5rem)] max-sm:max-w-[calc(100vw-1.5rem)] max-sm:p-4 sm:max-w-3xl lg:max-w-4xl'>
        <DialogHeader className='max-sm:gap-1'>
          <DialogTitle className='flex min-w-0 items-center gap-2 text-base'>
            <span className='min-w-0 truncate'>{t('Task Details')}</span>
            <StatusBadge
              label={t(taskStatusMapper.getLabel(log.status, log.status))}
              variant={taskStatusMapper.getVariant(log.status)}
              size='sm'
              copyable={false}
              showDot
            />
          </DialogTitle>
          <DialogDescription className='sr-only'>
            {t('View task details')}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className='max-h-[72vh] min-w-0 overflow-hidden pr-2 max-sm:max-h-[calc(100dvh-7rem)] sm:pr-4'>
          <div className='w-full max-w-full min-w-0 space-y-2.5 overflow-hidden py-1 sm:space-y-3'>
            <DetailSection
              icon={<Info className='size-3.5' aria-hidden='true' />}
              label={t('Task Overview')}
            >
              <DetailRow label={t('Task ID')} value={log.task_id || '-'} mono />
              <DetailRow
                label={t('Platform')}
                value={log.platform ? t(log.platform) : '-'}
                mono
              />
              <DetailRow
                label={t('Action')}
                value={t(taskActionMapper.getLabel(log.action, log.action))}
              />
              <DetailRow
                label={t('Status')}
                value={
                  <StatusBadge
                    label={t(taskStatusMapper.getLabel(log.status, log.status))}
                    variant={taskStatusMapper.getVariant(log.status)}
                    size='sm'
                    copyable={false}
                    showDot
                  />
                }
              />
              {log.progress && (
                <DetailRow label={t('Progress')} value={log.progress} mono />
              )}
              {log.progress_message_en && (
                <DetailRow
                  label={t('Progress Message')}
                  value={log.progress_message_en}
                />
              )}
              {props.isAdmin && (
                <>
                  <DetailRow
                    label={t('Channel')}
                    value={String(log.channel_id || '-')}
                    mono
                  />
                  <DetailRow
                    label={t('User')}
                    value={
                      log.username
                        ? `${log.username} (ID: ${log.user_id})`
                        : String(log.user_id || '-')
                    }
                    mono
                  />
                </>
              )}
            </DetailSection>

            <DetailSection
              icon={<Clock className='size-3.5' aria-hidden='true' />}
              label={t('Task Timing')}
            >
              <DetailRow
                label={t('Submit Time')}
                value={
                  log.submit_time
                    ? formatTimestampToDate(log.submit_time, 'seconds')
                    : '-'
                }
                mono
              />
              <DetailRow
                label={t('Finish Time')}
                value={
                  log.finish_time
                    ? formatTimestampToDate(log.finish_time, 'seconds')
                    : '-'
                }
                mono
              />
              <DetailRow
                label={t('Duration')}
                value={duration == null ? '-' : formatUseTime(duration)}
                mono
              />
              {log.created_at && (
                <DetailRow
                  label={t('Created At')}
                  value={formatTimestampToDate(log.created_at, 'seconds')}
                  mono
                />
              )}
              {log.updated_at && (
                <DetailRow
                  label={t('Updated At')}
                  value={formatTimestampToDate(log.updated_at, 'seconds')}
                  mono
                />
              )}
            </DetailSection>

            {log.fail_reason && (
              <DetailSection
                icon={
                  <AlertTriangle className='size-3.5' aria-hidden='true' />
                }
                label={t('Fail Reason')}
                variant='danger'
              >
                <p className='text-xs leading-relaxed break-words whitespace-pre-wrap text-red-600 dark:text-red-400'>
                  {log.fail_reason}
                </p>
              </DetailSection>
            )}

            <DetailSection
              icon={<ListTree className='size-3.5' aria-hidden='true' />}
              label={t('Input / Output')}
            >
              <DataSummary payload={dataPayload} />
              <UrlList urls={urls} />
            </DetailSection>

            <DetailSection
              icon={<FileJson className='size-3.5' aria-hidden='true' />}
              label={t('Raw Data')}
            >
              <JsonBlock
                label={dataPayload.ok ? t('Formatted JSON') : t('Raw Text')}
                payload={dataPayload}
                emptyLabel={t('No task data')}
                copiedText={copiedText}
                onCopy={copyToClipboard}
              />
            </DetailSection>

            <DetailSection
              icon={<Database className='size-3.5' aria-hidden='true' />}
              label={t('Raw Other')}
            >
              <JsonBlock
                label={otherPayload.ok ? t('Formatted JSON') : t('Raw Text')}
                payload={otherPayload}
                emptyLabel={t('No additional metadata')}
                copiedText={copiedText}
                onCopy={copyToClipboard}
              />
            </DetailSection>

            {props.isAdmin && (
              <DetailSection label={t('Internal Details')}>
                <DetailRow
                  label={t('Internal ID')}
                  value={String(log.id || '-')}
                  mono
                />
              </DetailSection>
            )}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
