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
import { createFileRoute, redirect } from '@tanstack/react-router'
import { useAuthStore } from '@/stores/auth-store'
import {
  isHeaderModuleEnabled,
  isSidebarModuleEnabled,
} from '@/lib/nav-modules'

const CANVAS_APP_SRC = '/canvas-app/canvas?v=20260805-1'
const CANVAS_BOOTSTRAP_STORAGE_KEY = 'openmind:canvas-bootstrap'

type CanvasCapability = 'image' | 'video' | 'text' | 'audio'

type CanvasBootstrapPayload = {
  user: {
    id: number
    username: string
  }
  baseUrl: string
  authMode: 'session'
  channelId: string
  channelName: string
  apiFormat: 'openai'
  group: string
  models: Array<{
    name: string
    capability: CanvasCapability
    group?: string
  }>
  defaults: {
    imageModel?: string
    videoModel?: string
    textModel?: string
    audioModel?: string
  }
}

type GroupModelsEnvelope = {
  success?: boolean
  data?: Record<string, string[]>
}

type GroupsEnvelope = {
  success?: boolean
  data?: Record<string, { desc?: string; ratio?: number }>
}

export const Route = createFileRoute('/_authenticated/canvas/')({
  beforeLoad: () => {
    if (
      !isHeaderModuleEnabled('canvas') ||
      !isSidebarModuleEnabled('chat', 'canvas')
    ) {
      throw redirect({ to: '/dashboard' })
    }
  },
  component: CanvasPage,
})

function CanvasPage() {
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    const bootstrap = async () => {
      try {
        const payload = await loadCanvasBootstrap()
        if (cancelled) return

        window.localStorage.setItem(
          CANVAS_BOOTSTRAP_STORAGE_KEY,
          JSON.stringify(payload)
        )
        setReady(true)
      } catch (err) {
        if (cancelled) return
        setError(
          err instanceof Error
            ? err.message
            : 'Unable to prepare canvas workspace configuration.'
        )
      }
    }

    void bootstrap()

    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <div className='bg-background flex h-full items-center justify-center p-6'>
        <div className='max-w-md space-y-3 text-center'>
          <h1 className='text-lg font-semibold'>
            Infinite Canvas is not ready
          </h1>
          <p className='text-muted-foreground text-sm'>{error}</p>
          <p className='text-muted-foreground text-sm'>
            Please refresh this page after your workspace session is ready.
          </p>
          <a
            className='hover:bg-accent inline-flex rounded-md border px-4 py-2 text-sm font-medium transition'
            href='/playground'
          >
            Open Playground
          </a>
        </div>
      </div>
    )
  }

  if (!ready) {
    return (
      <div className='bg-background flex h-full items-center justify-center p-6'>
        <div className='space-y-2 text-center'>
          <h1 className='text-lg font-semibold'>Preparing Infinite Canvas</h1>
          <p className='text-muted-foreground text-sm'>
            Loading your OpenMind workspace session and model defaults...
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className='bg-background h-full'>
      <iframe
        className='bg-background block h-full w-full border-0'
        src={CANVAS_APP_SRC}
        title='Infinite Canvas'
      />
    </div>
  )
}

async function loadCanvasBootstrap(): Promise<CanvasBootstrapPayload> {
  const user = useAuthStore.getState().auth.user
  if (!user) {
    throw new Error('Unable to identify the current user.')
  }

  const [groupModels, groups] = await Promise.all([
    fetchJson<GroupModelsEnvelope>('/api/user/group-models'),
    fetchJson<GroupsEnvelope>('/api/user/self/groups'),
  ])

  const availableGroups = Object.keys(groups.data || {})
  const group =
    availableGroups.find((item) => item === 'default') ||
    availableGroups[0] ||
    'default'

  const models = buildCanvasModels(groupModels.data || {})

  return {
    user: {
      id: user.id,
      username: user.username,
    },
    baseUrl: window.location.origin,
    authMode: 'session',
    channelId: 'openmind-session',
    channelName: 'OpenMind 工作台',
    apiFormat: 'openai',
    group,
    models,
    defaults: {
      imageModel: pickPreferredModel(models, 'image', [
        'gpt-image-2',
        'gemini-3-pro-image-preview',
        'gemini-3.1-flash-image-preview',
        'nano-banana-pro',
      ]),
      videoModel: pickPreferredModel(models, 'video', [
        'sd-2.0-fast-9ref',
        'sd2.5',
        'seedance-2.0-fast-720p',
        'seedance-2.0-720p',
        'seedance2.0',
        'sd2-mini',
        'happyhorse-720p',
        'happyhorse-1080p',
        'doubao-seedance-2-0-fast-260128',
        'doubao-seedance-2-0-260128',
        'grok-imagine-video-1.5-preview',
        'grok-imagine-video-1.5',
        'seedance2.0-900',
      ]),
      textModel: pickPreferredModel(models, 'text', [
        'gpt-5.5',
        'gpt-5.4',
        'claude-opus-4-8',
        'glm-5.2',
      ]),
      audioModel: pickPreferredModel(models, 'audio', ['gpt-4o-mini-tts']),
    },
  }
}

async function fetchJson<T>(input: string): Promise<T> {
  const response = await fetch(input, {
    credentials: 'include',
    cache: 'no-store',
    headers: buildSessionHeaders(),
  })

  if (!response.ok) {
    throw new Error(`Request failed with ${response.status}`)
  }

  return (await response.json()) as T
}

function buildSessionHeaders() {
  const headers = new Headers({
    'Cache-Control': 'no-store',
  })

  const uid = readStoredUserId()
  if (uid) {
    headers.set('New-Api-User', uid)
  }

  return headers
}

function readStoredUserId() {
  try {
    return window.localStorage.getItem('uid')
  } catch {
    return null
  }
}

function buildCanvasModels(groupModels: Record<string, string[]>) {
  const source = Object.entries(groupModels).flatMap(([group, modelNames]) =>
    (modelNames || []).filter(Boolean).map((name) => ({ name, group }))
  )

  const models = source.length
    ? source
    : [
        { name: 'gpt-image-2', group: 'default' },
        { name: 'sd-2.0-fast-9ref', group: 'default' },
        { name: 'sd2.5', group: 'default' },
        { name: 'seedance-2.0-fast-720p', group: 'default' },
        { name: 'grok-imagine-video-1.5-preview', group: 'default' },
        { name: 'gpt-5.5', group: 'default' },
      ]

  return models.map((item) => ({
    name: item.name,
    capability: guessCapability(item.name),
    group: item.group,
  }))
}

function pickPreferredModel(
  models: CanvasBootstrapPayload['models'],
  capability: CanvasCapability,
  preferred: string[]
) {
  const pool = models
    .filter((item) => item.capability === capability)
    .map((item) => item.name)

  return preferred.find((name) => pool.includes(name)) || pool[0] || ''
}

function guessCapability(name: string): CanvasCapability {
  const value = name.toLowerCase()

  if (
    [
      'seedance',
      'happyhorse',
      'sd2-mini',
      'sd2.5',
      'sd-2.0',
      'video',
      'sora',
      'veo',
      'kling',
      'wan',
      'hailuo',
      'grok-imagine-video',
    ].some((keyword) => value.includes(keyword))
  ) {
    return 'video'
  }

  if (
    ['audio', 'tts', 'speech', 'voice', 'music', 'sound'].some((keyword) =>
      value.includes(keyword)
    )
  ) {
    return 'audio'
  }

  if (
    [
      'seedream',
      'gpt-image',
      'image',
      'dall-e',
      'dalle',
      'imagen',
      'flux',
      'sdxl',
      'stable-diffusion',
      'midjourney',
      'nano-banana',
    ].some((keyword) => value.includes(keyword))
  ) {
    return 'image'
  }

  return 'text'
}
