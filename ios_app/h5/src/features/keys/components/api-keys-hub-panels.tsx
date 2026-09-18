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
import {
  ArrowRight,
  Bot,
  Download,
  ExternalLink,
  KeyRound,
  MonitorSmartphone,
  Network,
  Route,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { CopyButton } from '@/components/copy-button'

function getServerAddress() {
  try {
    const raw = localStorage.getItem('status')
    if (raw) {
      const status = JSON.parse(raw) as {
        server_address?: string
        data?: { server_address?: string }
      }
      if (status.server_address) return status.server_address
      if (status.data?.server_address) return status.data.server_address
    }
  } catch {
    /* empty */
  }

  if (typeof window !== 'undefined') {
    return window.location.origin
  }

  return 'https://www.openmindapi.com'
}

function EndpointCard(props: {
  title: string
  endpoint: string
  description: string
  usage: string
  warning?: string
}) {
  const { t } = useTranslation()

  return (
    <Card className='border-border/70 bg-background/80 gap-3 rounded-2xl py-0 shadow-sm'>
      <CardHeader className='border-border/60 border-b py-4'>
        <div className='flex items-start justify-between gap-3'>
          <div className='space-y-1.5'>
            <CardTitle className='text-sm font-semibold'>
              {props.title}
            </CardTitle>
            <CardDescription className='text-sm leading-6'>
              {props.description}
            </CardDescription>
          </div>
          <Badge variant='outline' className='shrink-0'>
            {props.usage}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className='space-y-3 py-4'>
        <div className='bg-muted/55 border-border/60 flex items-center gap-2 rounded-xl border px-3 py-3'>
          <code className='min-w-0 flex-1 font-mono text-sm break-all'>
            {props.endpoint}
          </code>
          <CopyButton
            value={props.endpoint}
            tooltip={t('Copy endpoint')}
            successTooltip={t('Endpoint copied')}
            className='size-8'
            iconClassName='size-3.5'
          />
        </div>
        {props.warning ? (
          <div className='rounded-xl border border-amber-200/80 bg-amber-50/90 px-3 py-2.5 text-sm leading-6 text-amber-800 dark:border-amber-900/70 dark:bg-amber-950/30 dark:text-amber-300'>
            {props.warning}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

function RuleItem(props: {
  title: string
  body: string
  tone?: 'default' | 'warning'
}) {
  return (
    <div
      className={cn(
        'rounded-2xl border px-4 py-3.5',
        props.tone === 'warning'
          ? 'border-amber-200/80 bg-amber-50/80 dark:border-amber-900/70 dark:bg-amber-950/30'
          : 'border-border/70 bg-background/80'
      )}
    >
      <div className='mb-1 text-sm font-semibold'>{props.title}</div>
      <p className='text-muted-foreground text-sm leading-6'>{props.body}</p>
    </div>
  )
}

function StepCard(props: {
  index: number
  title: string
  body: string
  note?: string
}) {
  return (
    <div className='border-border/70 bg-background/80 grid gap-3 rounded-2xl border px-4 py-4 sm:grid-cols-[36px_minmax(0,1fr)]'>
      <div className='bg-muted text-muted-foreground flex h-9 w-9 items-center justify-center rounded-xl text-sm font-semibold'>
        {props.index}
      </div>
      <div className='space-y-1.5'>
        <div className='text-sm font-semibold'>{props.title}</div>
        <p className='text-muted-foreground text-sm leading-6'>{props.body}</p>
        {props.note ? (
          <p className='text-sm leading-6 text-amber-700 dark:text-amber-300'>
            {props.note}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function ScreenshotCard(props: {
  src: string
  title: string
  description: string
}) {
  return (
    <Card className='border-border/70 bg-background/90 gap-0 rounded-2xl py-0 shadow-sm'>
      <div className='bg-muted/40 border-border/60 border-b p-2'>
        <img
          src={props.src}
          alt={props.title}
          className='border-border/60 h-auto w-full rounded-xl border object-cover'
        />
      </div>
      <CardContent className='space-y-1.5 py-4'>
        <div className='text-sm font-semibold'>{props.title}</div>
        <p className='text-muted-foreground text-sm leading-6'>
          {props.description}
        </p>
      </CardContent>
    </Card>
  )
}

export function PlatformAccessPanel() {
  const { t } = useTranslation()
  const origin = getServerAddress()
  const openAiEndpoint = `${origin}/v1`
  const anthropicEndpoint = origin

  return (
    <div className='space-y-5'>
      <section className='from-primary/[0.14] via-background border-border/60 rounded-[28px] border bg-linear-to-br to-emerald-500/[0.10] p-5 shadow-sm sm:p-6'>
        <div className='grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.9fr)]'>
          <div className='space-y-4'>
            <div className='flex flex-wrap items-center gap-2'>
              <Badge variant='outline' className='rounded-full px-3'>
                {t('Current platform access address')}
              </Badge>
              <Badge variant='secondary' className='rounded-full px-3'>
                {t('Same key, multiple clients')}
              </Badge>
            </div>
            <div className='space-y-2'>
              <h3 className='text-foreground text-xl font-semibold tracking-tight sm:text-2xl'>
                {t(
                  'Use one API Key across OpenAI, Codex, and Claude-style clients'
                )}
              </h3>
              <p className='text-muted-foreground max-w-3xl text-sm leading-7 sm:text-[15px]'>
                {t(
                  'This area puts the current platform endpoints in one place. Choose the correct base URL format first, then paste your API Key into the client.'
                )}
              </p>
            </div>
          </div>

          <div className='grid gap-3 sm:grid-cols-2 lg:grid-cols-1'>
            <div className='border-border/60 bg-background/80 rounded-2xl border px-4 py-4'>
              <div className='mb-2 flex items-center gap-2 text-sm font-semibold'>
                <Network className='size-4' />
                {t('Recommended for GPT / Codex / OpenAI-compatible clients')}
              </div>
              <p className='text-muted-foreground text-sm leading-6'>
                {t(
                  'Use the /v1 address when the client asks for OpenAI-compatible base URL, Chat Completions, or Codex routing endpoint.'
                )}
              </p>
            </div>
            <div className='border-border/60 bg-background/80 rounded-2xl border px-4 py-4'>
              <div className='mb-2 flex items-center gap-2 text-sm font-semibold'>
                <Bot className='size-4' />
                {t('Recommended for Claude / Anthropic-compatible clients')}
              </div>
              <p className='text-muted-foreground text-sm leading-6'>
                {t(
                  'Use the site root without appending /v1 when the client follows Claude or Anthropic endpoint rules.'
                )}
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className='grid gap-4 xl:grid-cols-2'>
        <EndpointCard
          title={t('OpenAI / GPT / Codex endpoint')}
          endpoint={openAiEndpoint}
          description={t(
            'For OpenAI SDK, GPT-compatible clients, Codex + local routing tools, and most Chat Completions integrations.'
          )}
          usage={t('Keep /v1')}
          warning={t(
            'If the client explicitly asks for an OpenAI-compatible base URL, do not remove /v1.'
          )}
        />
        <EndpointCard
          title={t('Claude / Anthropic endpoint')}
          endpoint={anthropicEndpoint}
          description={t(
            'For Claude-style clients or tools that call the Anthropic Messages API format.'
          )}
          usage={t('Root domain only')}
          warning={t(
            'For Claude-compatible clients, usually do not append /v1 manually unless the client documentation explicitly requires it.'
          )}
        />
      </div>

      <Card className='border-border/70 bg-background/90 rounded-2xl py-0 shadow-sm'>
        <CardHeader className='border-border/60 border-b py-4'>
          <div className='flex items-center gap-2'>
            <ShieldCheck className='text-primary size-4' />
            <CardTitle className='text-sm font-semibold'>
              {t('Quick connection rules')}
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent className='grid gap-3 py-4 lg:grid-cols-3'>
          <RuleItem
            title={t('Rule 1: create the key first')}
            body={t(
              'Create your API Key on this page first, then paste it into the client. Treat the key like a password and avoid sharing screenshots.'
            )}
          />
          <RuleItem
            title={t('Rule 2: endpoint format matters')}
            body={t(
              'GPT / OpenAI-compatible clients usually need the /v1 address, while Claude-compatible clients usually need the root domain.'
            )}
          />
          <RuleItem
            title={t('Rule 3: verify with actual usage logs')}
            body={t(
              'A successful connection test is not enough. Send a real request and confirm the call appears in this platform usage logs.'
            )}
            tone='warning'
          />
        </CardContent>
      </Card>
    </div>
  )
}

export function CodexToolsPanel(props: {
  onGoToAccess: () => void
  onGoToKeys: () => void
}) {
  const { t } = useTranslation()
  const origin = getServerAddress()
  const installerUrl =
    '/downloads/codex-tools/codex-launcher-openmind-installer.exe'
  const screenshots = useMemo(
    () => [
      {
        src: '/images/codex-tools/codex-tool-overview.png',
        title: t('Overview panel'),
        description: t(
          'The launcher puts install status, version switching, and startup entry in one compact home view.'
        ),
      },
      {
        src: '/images/codex-tools/codex-tool-version-manager.png',
        title: t('Version management'),
        description: t(
          'Keep recent releases, launch a specific local version, and manage retained history without touching command line tools.'
        ),
      },
      {
        src: '/images/codex-tools/codex-tool-settings.png',
        title: t('Retention and maintenance settings'),
        description: t(
          'Set update strategy, retention count, and desktop shortcut behavior in a more user-friendly workflow.'
        ),
      },
    ],
    [t]
  )

  return (
    <div className='space-y-5'>
      <section className='via-background border-border/60 rounded-[28px] border bg-linear-to-br from-sky-500/[0.16] to-emerald-500/[0.12] p-5 shadow-sm sm:p-6'>
        <div className='grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(340px,0.9fr)]'>
          <div className='space-y-4'>
            <div className='flex flex-wrap items-center gap-2'>
              <Badge variant='outline' className='rounded-full px-3'>
                {t('Codex / Claude Code free tool')}
              </Badge>
              <Badge variant='secondary' className='rounded-full px-3'>
                {t('Windows 10 / 11')}
              </Badge>
              <Badge variant='secondary' className='rounded-full px-3'>
                {t('Local setup helper')}
              </Badge>
            </div>
            <div className='space-y-2'>
              <h3 className='text-foreground text-xl font-semibold tracking-tight sm:text-2xl'>
                {t(
                  'Download the local helper, then finish account setup with the current platform endpoint'
                )}
              </h3>
              <p className='text-muted-foreground max-w-3xl text-sm leading-7 sm:text-[15px]'>
                {t(
                  'This tool page is for users who want a simpler Windows-side setup flow. It combines installer download, key preparation, endpoint reminder, and operation screenshots in one place.'
                )}
              </p>
            </div>
            <div className='flex flex-wrap gap-2'>
              <Button
                size='lg'
                render={
                  <a
                    href={installerUrl}
                    download='codex-launcher-openmind-installer.exe'
                  />
                }
              >
                <Download className='size-4' />
                {t('Download installer')}
              </Button>
              <Button variant='outline' size='lg' onClick={props.onGoToAccess}>
                <Route className='size-4' />
                {t('View platform endpoint')}
              </Button>
              <Button variant='ghost' size='lg' onClick={props.onGoToKeys}>
                <KeyRound className='size-4' />
                {t('Back to API Keys')}
              </Button>
            </div>
          </div>

          <Card className='border-border/70 bg-background/88 rounded-3xl py-0 shadow-sm'>
            <CardHeader className='border-border/60 border-b py-4'>
              <div className='flex items-center gap-2'>
                <MonitorSmartphone className='text-primary size-4' />
                <CardTitle className='text-sm font-semibold'>
                  {t('Installer package info')}
                </CardTitle>
              </div>
            </CardHeader>
            <CardContent className='space-y-3 py-4'>
              <div className='border-border/60 bg-muted/45 rounded-2xl border px-3 py-3'>
                <div className='text-muted-foreground mb-1 text-xs'>
                  {t('File name')}
                </div>
                <div className='font-mono text-sm'>
                  codex-launcher-openmind-installer.exe
                </div>
              </div>
              <div className='grid gap-3 sm:grid-cols-2'>
                <div className='border-border/60 bg-background/80 rounded-2xl border px-3 py-3'>
                  <div className='text-muted-foreground mb-1 text-xs'>
                    {t('Package size')}
                  </div>
                  <div className='text-sm font-semibold'>4.66 MB</div>
                </div>
                <div className='border-border/60 bg-background/80 rounded-2xl border px-3 py-3'>
                  <div className='text-muted-foreground mb-1 text-xs'>
                    {t('Source')}
                  </div>
                  <div className='text-sm font-semibold'>
                    {t('Local build package')}
                  </div>
                </div>
              </div>
              <div className='border-border/60 bg-background/80 rounded-2xl border px-3 py-3'>
                <div className='text-muted-foreground mb-1 text-xs'>
                  {t('SHA256')}
                </div>
                <code className='block font-mono text-[12px] leading-6 break-all'>
                  86643a0ddc6db634d409e365b1ac94c93965784dc101bdc1910d584369e2d47d
                </code>
              </div>
              <Button
                variant='outline'
                className='w-full'
                render={
                  <a
                    href='https://github.com/chrichuang218/codex-windows-cn/releases/latest'
                    target='_blank'
                    rel='noreferrer'
                  />
                }
              >
                <ExternalLink className='size-4' />
                {t('Open upstream release notes')}
              </Button>
            </CardContent>
          </Card>
        </div>
      </section>

      <Card className='border-border/70 bg-background/90 rounded-2xl py-0 shadow-sm'>
        <CardHeader className='border-border/60 border-b py-4'>
          <div className='flex items-center gap-2'>
            <Sparkles className='text-primary size-4' />
            <CardTitle className='text-sm font-semibold'>
              {t('Recommended setup path')}
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent className='grid gap-3 py-4'>
          <StepCard
            index={1}
            title={t('Download and install the helper')}
            body={t(
              'Start by downloading the installer on this page. Finish the Windows installation first so the local helper and version manager are ready.'
            )}
          />
          <StepCard
            index={2}
            title={t('Create an API Key on this platform')}
            body={t(
              'After purchase or recharge, come back to this API Keys page and create a new key dedicated to the client or tool you are configuring.'
            )}
          />
          <StepCard
            index={3}
            title={t('Use the correct endpoint format')}
            body={t(
              'For Codex, GPT, and most OpenAI-compatible clients use {{endpoint}}. For Claude-compatible clients use the root domain {{root}}.',
              {
                endpoint: `${origin}/v1`,
                root: origin,
              }
            )}
            note={t(
              'If the client asks for OpenAI-compatible base URL, keep /v1. If it follows Claude endpoint rules, usually do not append /v1.'
            )}
          />
          <StepCard
            index={4}
            title={t('Send one real request and verify logs')}
            body={t(
              'Connection test alone is not enough. After setup, send one real prompt and verify the request appears in the platform usage logs.'
            )}
          />
        </CardContent>
      </Card>

      <div className='grid gap-4 xl:grid-cols-3'>
        {screenshots.map((shot) => (
          <ScreenshotCard
            key={shot.src}
            src={shot.src}
            title={shot.title}
            description={shot.description}
          />
        ))}
      </div>

      <Card className='border-border/70 bg-background/90 rounded-2xl py-0 shadow-sm'>
        <CardHeader className='border-border/60 border-b py-4'>
          <div className='flex items-center gap-2'>
            <ShieldCheck className='text-primary size-4' />
            <CardTitle className='text-sm font-semibold'>
              {t('Safety reminder')}
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent className='space-y-3 py-4'>
          <div className='rounded-2xl border border-amber-200/80 bg-amber-50/80 px-4 py-3 text-sm leading-6 text-amber-800 dark:border-amber-900/70 dark:bg-amber-950/30 dark:text-amber-300'>
            {t(
              'Do not share API Keys in screenshots, chats, or public repositories. If you suspect exposure, delete the old key and create a new one immediately.'
            )}
          </div>
          <div className='flex flex-wrap gap-2'>
            <Button variant='outline' onClick={props.onGoToAccess}>
              <Network className='size-4' />
              {t('Check endpoint again')}
            </Button>
            <Button variant='outline' onClick={props.onGoToKeys}>
              <ArrowRight className='size-4' />
              {t('Create or view API Keys')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
