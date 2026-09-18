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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MenuIcon } from 'lucide-react'
import { nanoid } from 'nanoid'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  createPlaygroundConversation,
  deletePlaygroundConversation,
  getImageTask,
  getPlaygroundConversation,
  getUserGroupModels,
  getUserGroups,
  getVideoTask,
  listPlaygroundConversations,
  submitImageTask,
  submitVideo,
  syncPlaygroundConversation,
  updatePlaygroundConversation,
} from './api'
import { PlaygroundChat } from './components/playground-chat'
import { PlaygroundHistorySidebar } from './components/playground-history-sidebar'
import { PlaygroundInput } from './components/playground-input'
import { usePlaygroundState, useChatHandler } from './hooks'
import {
  buildConversationSyncPayload,
  createLoadingAssistantMessage,
  createUserMessage,
  getImageModelPreset,
  getConversationTitle,
  isImageModel,
  isVideoModel,
  restoreGenerationResults,
  restoreMessages,
} from './lib'
import type {
  GenerationResult,
  ImageTaskResponse,
  Message as MessageType,
  MessageAttachment,
  PlaygroundMode,
  VideoGenerationResponse,
} from './types'

type UploadedFile = {
  id: string
  name: string
  type: string
  url: string
  size: number
}

type ImageAttachmentRequest = UploadedFile

const VIDEO_DONE_STATUSES = new Set(['completed', 'succeeded', 'success'])
const VIDEO_FAILED_STATUSES = new Set(['failed', 'failure', 'cancelled'])
const IMAGE_DONE_STATUSES = new Set(['completed', 'succeeded', 'success'])
const IMAGE_FAILED_STATUSES = new Set(['failed', 'failure', 'cancelled'])
const CONVERSATIONS_QUERY_KEY = ['playground-conversations']

function getPlaygroundVideoContentUrl(taskId: string) {
  return `/api/public/videos/${encodeURIComponent(taskId)}/content`
}

function normalizeVideoTaskResponse(response: VideoGenerationResponse) {
  return response.data ?? response
}

function normalizeVideoStatus(status?: string) {
  switch ((status || '').toLowerCase()) {
    case 'success':
      return 'completed'
    case 'failure':
      return 'failed'
    case 'in_progress':
      return 'processing'
    default:
      return (status || '').toLowerCase()
  }
}

function normalizeVideoProgress(progress?: number | string) {
  if (typeof progress === 'number') return progress
  if (typeof progress === 'string') {
    const value = Number(progress.replace('%', ''))
    return Number.isFinite(value) ? value : 0
  }
  return 0
}

function hasVideoResult(task: VideoGenerationResponse) {
  return Boolean(task.video_url || task.result_url || task.url)
}

function normalizeTaskStatus(status?: string) {
  switch ((status || '').toLowerCase()) {
    case 'success':
      return 'completed'
    case 'failure':
      return 'failed'
    case 'in_progress':
      return 'processing'
    default:
      return (status || '').toLowerCase()
  }
}

function extractImageTaskImages(task: ImageTaskResponse) {
  const images: Array<{ url: string; revisedPrompt?: string }> =
    task.data?.data?.flatMap((item) => {
      const url = item.url
        ? item.url
        : item.b64_json
          ? `data:image/png;base64,${item.b64_json}`
          : ''
      return url ? [{ url, revisedPrompt: item.revised_prompt }] : []
    }) ?? []

  for (const url of [
    task.image_url,
    task.result_url,
    task.url,
    task.output?.url,
    task.result?.url,
  ]) {
    if (url && !images.some((item) => item.url === url)) {
      images.push({ url })
    }
  }

  return images
}

function getModeModels(
  models: Array<{ label: string; value: string }>,
  mode: PlaygroundMode
) {
  if (mode === 'image') {
    const imageModels = models.filter(
      (model) => isImageModel(model.value) && !isVideoModel(model.value)
    )
    return imageModels.length > 0 ? imageModels : models
  }
  if (mode === 'video') {
    const videoModels = models.filter((model) => isVideoModel(model.value))
    return videoModels.length > 0 ? videoModels : models
  }
  return models.filter(
    (model) => !isImageModel(model.value) && !isVideoModel(model.value)
  )
}

function getPreferredModeModel(
  models: Array<{ label: string; value: string }>,
  mode: PlaygroundMode
) {
  if (!models.length) return null

  if (mode === 'image') {
    return (
      models.find(
        (model) => model.value.trim().toLowerCase() === 'gpt-image-2'
      ) ?? models[0]
    )
  }

  return models[0]
}

function shouldSendVideoMetadata(model: string) {
  return /^(?:veo-\d|veo-\d+\.\d+)/i.test(model)
}

function replaceGenerationResult(
  results: GenerationResult[],
  result: GenerationResult
) {
  let replaced = false
  const next = results.map((item) => {
    if (item.id !== result.id) return item
    replaced = true
    return result
  })
  return replaced ? next : [result, ...next]
}

export function Playground() {
  const { t } = useTranslation()
  const {
    config,
    parameterEnabled,
    messages,
    models,
    groups,
    updateMessages,
    replaceMessages,
    clearMessages,
    setModels,
    setGroups,
    updateConfig,
  } = usePlaygroundState()
  const queryClient = useQueryClient()

  const { sendChat, stopGeneration, isGenerating } = useChatHandler({
    config,
    parameterEnabled,
    onMessageUpdate: updateMessages,
  })

  // Edit dialog state
  const [editingMessageKey, setEditingMessageKey] = useState<string | null>(
    null
  )
  const [mode, setMode] = useState<PlaygroundMode>('chat')
  const [generationResults, setGenerationResults] = useState<
    GenerationResult[]
  >([])
  const [imageAttachmentRequest, setImageAttachmentRequest] =
    useState<ImageAttachmentRequest | null>(null)
  const [isGeneratingMedia, setIsGeneratingMedia] = useState(false)
  const [activeConversationId, setActiveConversationId] = useState<
    number | null
  >(null)
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)
  const [isRestoringConversation, setIsRestoringConversation] = useState(false)
  const latestConversationIdRef = useRef<number | null>(null)
  const latestMessagesRef = useRef<MessageType[]>(messages)
  const latestGenerationResultsRef =
    useRef<GenerationResult[]>(generationResults)

  const handleUseGeneratedImage = useCallback(
    ({
      url,
      result,
      index,
    }: {
      url: string
      displayUrl: string
      result: Extract<GenerationResult, { mode: 'image' }>
      index: number
    }) => {
      if (!url) return
      setMode('chat')
      setImageAttachmentRequest({
        id: nanoid(),
        name: `${result.model || 'generated-image'}-${index + 1}.png`,
        size: 0,
        type: 'image/png',
        url,
      })
    },
    []
  )

  // Load group models (groups with their models)
  const { data: groupModelsData, isLoading: isLoadingModels } = useQuery({
    queryKey: ['playground-group-models'],
    queryFn: getUserGroupModels,
  })

  // Load groups
  const { data: groupsData } = useQuery({
    queryKey: ['playground-groups'],
    queryFn: getUserGroups,
  })

  const { data: conversationsData, isLoading: isLoadingConversations } =
    useQuery({
      queryKey: CONVERSATIONS_QUERY_KEY,
      queryFn: listPlaygroundConversations,
    })

  const conversations = conversationsData?.items ?? []

  // Compute models based on selected group
  const availableModels = useMemo(() => {
    if (!groupModelsData || !config.group) return []

    const groupModels = groupModelsData[config.group] || []
    return groupModels.map((model) => ({
      label: model,
      value: model,
    }))
  }, [groupModelsData, config.group])

  const modeModels = useMemo(() => getModeModels(models, mode), [models, mode])

  // Update models when group changes
  useEffect(() => {
    if (!availableModels.length) return

    setModels(availableModels)
    const nextModeModels = getModeModels(availableModels, mode)
    const preferredModeModel = getPreferredModeModel(nextModeModels, mode)

    // If current model is not in the new group's models, switch to first available
    const isCurrentModelValid = availableModels.some(
      (m) => m.value === config.model
    )
    if (!isCurrentModelValid) {
      updateConfig(
        'model',
        preferredModeModel?.value ?? availableModels[0].value
      )
    }
  }, [availableModels, config.model, mode, setModels, updateConfig])

  useEffect(() => {
    if (!modeModels.length) return
    if (!modeModels.some((model) => model.value === config.model)) {
      const preferredModeModel = getPreferredModeModel(modeModels, mode)
      if (preferredModeModel) {
        updateConfig('model', preferredModeModel.value)
      }
    }
  }, [config.model, mode, modeModels, updateConfig])

  // Update groups when data changes
  useEffect(() => {
    if (!groupsData) return

    setGroups(groupsData)

    const hasCurrentGroup = groupsData.some((g) => g.value === config.group)
    if (!hasCurrentGroup && groupsData.length > 0) {
      const fallback =
        groupsData.find((g) => g.value === 'default')?.value ??
        groupsData[0].value
      updateConfig('group', fallback)
    }
  }, [groupsData, setGroups, config.group, updateConfig])

  useEffect(() => {
    latestConversationIdRef.current = activeConversationId
  }, [activeConversationId])

  useEffect(() => {
    latestMessagesRef.current = messages
  }, [messages])

  useEffect(() => {
    latestGenerationResultsRef.current = generationResults
  }, [generationResults])

  const refreshConversations = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: CONVERSATIONS_QUERY_KEY })
  }, [queryClient])

  const ensureConversation = useCallback(
    async (title: string) => {
      if (latestConversationIdRef.current) {
        return latestConversationIdRef.current
      }

      const conversation = await createPlaygroundConversation({
        title,
        last_model: config.model,
        last_mode: mode,
      })
      setActiveConversationId(conversation.id)
      latestConversationIdRef.current = conversation.id
      refreshConversations()
      return conversation.id
    },
    [config.model, mode, refreshConversations]
  )

  const handleNewConversation = useCallback(() => {
    stopGeneration()
    setActiveConversationId(null)
    latestConversationIdRef.current = null
    clearMessages()
    setGenerationResults([])
    setEditingMessageKey(null)
    setIsHistoryOpen(false)
  }, [clearMessages, stopGeneration])

  const handleSelectConversation = useCallback(
    async (id: number) => {
      stopGeneration()
      setIsRestoringConversation(true)
      try {
        const detail = await getPlaygroundConversation(id)
        setActiveConversationId(id)
        latestConversationIdRef.current = id
        replaceMessages(restoreMessages(detail.messages))
        setGenerationResults(restoreGenerationResults(detail.messages))
        if (detail.conversation.last_mode === 'image') setMode('image')
        if (detail.conversation.last_mode === 'video') setMode('video')
        if (detail.conversation.last_mode === 'chat') setMode('chat')
        if (detail.conversation.last_model) {
          updateConfig('model', detail.conversation.last_model)
        }
        setEditingMessageKey(null)
        setIsHistoryOpen(false)
      } finally {
        setTimeout(() => setIsRestoringConversation(false), 0)
      }
    },
    [replaceMessages, stopGeneration, updateConfig]
  )

  const handleRenameConversation = useCallback(
    async (id: number, title: string) => {
      await updatePlaygroundConversation(id, { title })
      refreshConversations()
    },
    [refreshConversations]
  )

  const handleDeleteConversation = useCallback(
    async (id: number) => {
      await deletePlaygroundConversation(id)
      refreshConversations()
      if (id === latestConversationIdRef.current) {
        handleNewConversation()
      }
    },
    [handleNewConversation, refreshConversations]
  )

  const syncCurrentConversation = useCallback(async () => {
    const conversationId = latestConversationIdRef.current
    if (!conversationId || isRestoringConversation) return
    if (messages.length === 0 && generationResults.length === 0) return

    const title = getConversationTitle(messages, t('New conversation'))
    const payload = buildConversationSyncPayload({
      messages,
      generationResults,
      model: config.model,
      mode,
      title,
    })
    await syncPlaygroundConversation(conversationId, payload)
    refreshConversations()
  }, [
    config.model,
    generationResults,
    isRestoringConversation,
    messages,
    mode,
    refreshConversations,
    t,
  ])

  const syncConversationSnapshot = useCallback(
    async ({
      conversationId,
      snapshotMessages,
      snapshotGenerationResults,
      snapshotModel,
      snapshotMode,
      fallbackTitle,
    }: {
      conversationId: number
      snapshotMessages: MessageType[]
      snapshotGenerationResults: GenerationResult[]
      snapshotModel: string
      snapshotMode: PlaygroundMode
      fallbackTitle: string
    }) => {
      const title = getConversationTitle(snapshotMessages, fallbackTitle)
      const payload = buildConversationSyncPayload({
        messages: snapshotMessages,
        generationResults: snapshotGenerationResults,
        model: snapshotModel,
        mode: snapshotMode,
        title,
      })
      await syncPlaygroundConversation(conversationId, payload)
      refreshConversations()
    },
    [refreshConversations]
  )

  useEffect(() => {
    if (!activeConversationId || isRestoringConversation) return
    const timer = window.setTimeout(() => {
      syncCurrentConversation().catch(() => {
        // Background sync failure should not interrupt model generation.
      })
    }, 800)
    return () => window.clearTimeout(timer)
  }, [
    activeConversationId,
    generationResults,
    isRestoringConversation,
    messages,
    syncCurrentConversation,
  ])

  const handleSendMessage = async (text: string, files?: UploadedFile[]) => {
    await ensureConversation(text)
    // Create user message with attachments
    const attachments: MessageAttachment[] | undefined = files?.map((f) => ({
      id: f.id,
      name: f.name,
      type: f.type,
      url: f.url,
      size: f.size,
    }))

    const userMessage = createUserMessage(text, attachments)
    const assistantMessage = createLoadingAssistantMessage()

    const newMessages = [...messages, userMessage, assistantMessage]
    updateMessages(newMessages)

    // Send chat request
    sendChat(newMessages)
  }

  const updateGenerationResult = useCallback(
    (id: string, patch: Partial<GenerationResult>) => {
      setGenerationResults((prev) =>
        prev.map((item) =>
          item.id === id ? ({ ...item, ...patch } as GenerationResult) : item
        )
      )
    },
    []
  )

  const pollImageTask = useCallback(
    async ({
      taskId,
      initialResult,
      conversationId,
      snapshotMessages,
      snapshotModel,
      snapshotMode,
      fallbackTitle,
    }: {
      taskId: string
      initialResult: Extract<GenerationResult, { mode: 'image' }>
      conversationId: number
      snapshotMessages: MessageType[]
      snapshotModel: string
      snapshotMode: PlaygroundMode
      fallbackTitle: string
    }) => {
      for (let attempt = 0; attempt < 180; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5000))
        const task = await getImageTask(taskId)
        const status = normalizeTaskStatus(task.status)

        if (IMAGE_DONE_STATUSES.has(status)) {
          const images = extractImageTaskImages(task)
          if (images.length === 0) {
            throw new Error(task.error?.message || 'No image returned')
          }

          const completedResult: GenerationResult = {
            ...initialResult,
            status: 'complete',
            images,
          }
          const snapshotResults = replaceGenerationResult(
            latestGenerationResultsRef.current,
            completedResult
          )
          latestGenerationResultsRef.current = snapshotResults
          setGenerationResults(snapshotResults)
          await syncConversationSnapshot({
            conversationId,
            snapshotMessages,
            snapshotGenerationResults: snapshotResults,
            snapshotModel,
            snapshotMode,
            fallbackTitle,
          })
          return
        }

        if (IMAGE_FAILED_STATUSES.has(status)) {
          throw new Error(task.error?.message || status || 'Image task failed')
        }
      }

      throw new Error('Image task timed out')
    },
    [syncConversationSnapshot]
  )

  const handleGenerateImage = useCallback(
    async (
      text: string,
      options: { size: string; quality: string; n: number },
      files?: UploadedFile[]
    ) => {
      const conversationId = await ensureConversation(text)
      const resultId = nanoid()
      const imageFiles = files?.filter((file) => file.type.startsWith('image/'))
      const initialResult: Extract<GenerationResult, { mode: 'image' }> = {
        id: resultId,
        mode: 'image',
        prompt: text,
        status: 'loading',
        model: config.model,
        createdAt: Date.now(),
        images: [],
      }
      const snapshotMessages = latestMessagesRef.current
      let snapshotResults = [
        initialResult,
        ...latestGenerationResultsRef.current,
      ]
      setGenerationResults(snapshotResults)
      latestGenerationResultsRef.current = snapshotResults
      setIsGeneratingMedia(true)

      try {
        const imageUrls = imageFiles?.map((file) => file.url) ?? []
        const imagePreset = getImageModelPreset(config.model)
        const isGPTImage2 = config.model.toLowerCase() === 'gpt-image-2'
        const hasInputImages = imageUrls.length > 0
        const response = await submitImageTask({
          model: config.model,
          group: config.group,
          prompt: text,
          size: options.size,
          quality: options.quality,
          resolution: imagePreset.includeResolution
            ? options.quality
            : undefined,
          aspect_ratio: imagePreset.includeAspectRatio
            ? options.size
            : undefined,
          image_size: imagePreset.includeImageSize
            ? options.quality
            : undefined,
          n: options.n,
          response_format: 'url',
          images: imageUrls.length > 0 ? imageUrls : undefined,
          image:
            isGPTImage2 && hasInputImages
              ? imageUrls
              : imageUrls.length === 1
                ? imageUrls[0]
                : undefined,
          input_fidelity: isGPTImage2 && hasInputImages ? 'high' : undefined,
        })
        const taskId = response.task_id || response.id
        if (!taskId) {
          throw new Error(response.error?.message || 'No task id returned')
        }

        const immediateImages = extractImageTaskImages(response)
        const immediateStatus = normalizeTaskStatus(response.status)

        if (
          immediateImages.length > 0 &&
          (IMAGE_DONE_STATUSES.has(immediateStatus) || !immediateStatus)
        ) {
          const completedResult: GenerationResult = {
            ...initialResult,
            status: 'complete',
            images: immediateImages,
          }
          snapshotResults = replaceGenerationResult(
            latestGenerationResultsRef.current,
            completedResult
          )
          latestGenerationResultsRef.current = snapshotResults
          setGenerationResults(snapshotResults)
          await syncConversationSnapshot({
            conversationId,
            snapshotMessages,
            snapshotGenerationResults: snapshotResults,
            snapshotModel: config.model,
            snapshotMode: mode,
            fallbackTitle: text,
          })
          return
        }

        await syncConversationSnapshot({
          conversationId,
          snapshotMessages,
          snapshotGenerationResults: latestGenerationResultsRef.current,
          snapshotModel: config.model,
          snapshotMode: mode,
          fallbackTitle: text,
        })
        await pollImageTask({
          taskId,
          initialResult,
          conversationId,
          snapshotMessages,
          snapshotModel: config.model,
          snapshotMode: mode,
          fallbackTitle: text,
        })
      } catch (error) {
        const err = error as {
          response?: {
            data?: { error?: { message?: string }; message?: string }
          }
          message?: string
        }
        const failedResult: GenerationResult = {
          ...initialResult,
          status: 'error',
          error:
            err.response?.data?.error?.message ||
            err.response?.data?.message ||
            err.message,
        }
        snapshotResults = replaceGenerationResult(
          latestGenerationResultsRef.current,
          failedResult
        )
        latestGenerationResultsRef.current = snapshotResults
        setGenerationResults(snapshotResults)
        await syncConversationSnapshot({
          conversationId,
          snapshotMessages,
          snapshotGenerationResults: snapshotResults,
          snapshotModel: config.model,
          snapshotMode: mode,
          fallbackTitle: text,
        })
      } finally {
        setIsGeneratingMedia(false)
      }
    },
    [
      config.group,
      config.model,
      ensureConversation,
      mode,
      pollImageTask,
      syncConversationSnapshot,
    ]
  )

  const pollVideoTask = useCallback(
    async (resultId: string, taskId: string) => {
      for (let attempt = 0; attempt < 120; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 5000))
        const task = normalizeVideoTaskResponse(await getVideoTask(taskId))
        const status = normalizeVideoStatus(task.status)
        const progress = normalizeVideoProgress(task.progress)
        const videoUrl = getPlaygroundVideoContentUrl(taskId)

        if (VIDEO_DONE_STATUSES.has(status)) {
          updateGenerationResult(resultId, {
            status: 'complete',
            progress: 100,
            videoUrl,
          } as Partial<GenerationResult>)
          return
        }

        if (VIDEO_FAILED_STATUSES.has(status)) {
          updateGenerationResult(resultId, {
            status: 'error',
            error: task.error?.message || status,
          } as Partial<GenerationResult>)
          return
        }

        updateGenerationResult(resultId, {
          status: 'processing',
          progress,
          videoUrl: hasVideoResult(task) ? videoUrl : undefined,
        } as Partial<GenerationResult>)
      }

      updateGenerationResult(resultId, {
        status: 'processing',
      } as Partial<GenerationResult>)
    },
    [updateGenerationResult]
  )

  const handleGenerateVideo = useCallback(
    async (
      text: string,
      options: {
        aspectRatio: string
        duration: number
        size: string
        generateAudio: boolean
      },
      files?: UploadedFile[]
    ) => {
      await ensureConversation(text)
      const resultId = nanoid()
      const imageFiles = files?.filter((file) => file.type.startsWith('image/'))
      setGenerationResults((prev) => [
        {
          id: resultId,
          mode: 'video',
          prompt: text,
          status: 'loading',
          model: config.model,
          createdAt: Date.now(),
          progress: 0,
        },
        ...prev,
      ])
      setIsGeneratingMedia(true)

      try {
        const imageUrls = imageFiles?.map((file) => file.url) ?? []
        const isVeo31 = /^veo31(?:-|$)/i.test(config.model)
        const withVideoMetadata = shouldSendVideoMetadata(config.model)
        const veoSize = `${options.aspectRatio.replace(':', 'x')}-${options.size}`
        const response = await submitVideo({
          model: config.model,
          group: config.group,
          prompt: text,
          aspect_ratio: options.aspectRatio,
          duration: options.duration,
          seconds: String(options.duration),
          size: isVeo31 ? veoSize : options.size,
          resolution: options.size,
          image: imageUrls[0],
          image_urls: imageUrls.length > 0 ? imageUrls : undefined,
          reference_images: imageUrls.length > 0 ? imageUrls : undefined,
          generate_audio: options.generateAudio,
          metadata: withVideoMetadata
            ? {
                durationSeconds: options.duration,
                resolution: options.size,
                aspectRatio: options.aspectRatio,
              }
            : undefined,
        })
        const taskId = response.task_id || response.id
        if (!taskId) {
          throw new Error(response.error?.message || 'No task id returned')
        }

        updateGenerationResult(resultId, {
          status: 'processing',
          taskId,
          progress: normalizeVideoProgress(response.progress),
          videoUrl: hasVideoResult(response)
            ? getPlaygroundVideoContentUrl(taskId)
            : undefined,
        } as Partial<GenerationResult>)

        if (
          hasVideoResult(response) ||
          VIDEO_DONE_STATUSES.has(normalizeVideoStatus(response.status))
        ) {
          updateGenerationResult(resultId, {
            status: 'complete',
            progress: 100,
            videoUrl: getPlaygroundVideoContentUrl(taskId),
          } as Partial<GenerationResult>)
          return
        }

        await pollVideoTask(resultId, taskId)
      } catch (error) {
        const err = error as {
          response?: {
            data?: { error?: { message?: string }; message?: string }
          }
          message?: string
        }
        updateGenerationResult(resultId, {
          status: 'error',
          error:
            err.response?.data?.error?.message ||
            err.response?.data?.message ||
            err.message,
        } as Partial<GenerationResult>)
      } finally {
        setIsGeneratingMedia(false)
      }
    },
    [config.group, config.model, pollVideoTask, updateGenerationResult]
  )

  const handleCopyMessage = (message: MessageType) => {
    // Copy is handled in MessageActions component
    // eslint-disable-next-line no-console
    console.log('Message copied:', message.key)
  }

  const handleRegenerateMessage = (message: MessageType) => {
    // Find the message index and regenerate from there
    const messageIndex = messages.findIndex((m) => m.key === message.key)
    if (messageIndex === -1) return

    // Remove messages after this one and regenerate
    const messagesUpToHere = messages.slice(0, messageIndex)
    const loadingMessage = createLoadingAssistantMessage()
    const newMessages = [...messagesUpToHere, loadingMessage]

    updateMessages(newMessages)
    sendChat(newMessages)
  }

  const handleEditMessage = useCallback((message: MessageType) => {
    setEditingMessageKey(message.key)
  }, [])

  const handleEditOpenChange = useCallback((open: boolean) => {
    if (!open) setEditingMessageKey(null)
  }, [])

  // Apply edit and optionally re-submit from the edited user message
  const applyEdit = useCallback(
    (newContent: string, submit: boolean) => {
      if (!editingMessageKey) return
      const index = messages.findIndex((m) => m.key === editingMessageKey)
      if (index === -1) return

      const updated = messages.map((m) =>
        m.key === editingMessageKey
          ? { ...m, versions: [{ ...m.versions[0], content: newContent }] }
          : m
      )

      setEditingMessageKey(null)

      if (!submit || updated[index].from !== 'user') {
        updateMessages(updated)
        return
      }

      const toSubmit = [
        ...updated.slice(0, index + 1),
        createLoadingAssistantMessage(),
      ]
      updateMessages(toSubmit)
      sendChat(toSubmit)
    },
    [editingMessageKey, messages, updateMessages, sendChat]
  )

  const handleDeleteMessage = (message: MessageType) => {
    const newMessages = messages.filter((m) => m.key !== message.key)
    updateMessages(newMessages)
  }

  const renderHistorySidebar = () => (
    <PlaygroundHistorySidebar
      activeId={activeConversationId}
      className='bg-card w-full shadow-sm'
      conversations={conversations}
      isLoading={isLoadingConversations}
      onDeleteConversation={handleDeleteConversation}
      onNewConversation={handleNewConversation}
      onRenameConversation={handleRenameConversation}
      onSelectConversation={handleSelectConversation}
    />
  )

  return (
    <div className='grid h-full min-h-[540px] gap-3 lg:grid-cols-[16rem_minmax(0,1fr)]'>
      <div className='hidden min-h-0 lg:block'>{renderHistorySidebar()}</div>
      <Sheet open={isHistoryOpen} onOpenChange={setIsHistoryOpen}>
        <SheetContent side='left' className='w-80 rounded-none p-0 sm:max-w-80'>
          <SheetHeader className='sr-only'>
            <SheetTitle>{t('Conversation history')}</SheetTitle>
          </SheetHeader>
          {renderHistorySidebar()}
        </SheetContent>
      </Sheet>

      <div className='bg-card flex min-h-0 min-w-0 flex-col overflow-hidden rounded-lg border shadow-sm'>
        <div className='bg-background/60 border-b px-3 py-2 lg:hidden'>
          <Button
            size='sm'
            variant='outline'
            onClick={() => setIsHistoryOpen(true)}
          >
            <MenuIcon className='size-4' />
            {t('History')}
          </Button>
        </div>

        <div className='flex min-h-0 flex-1 flex-col overflow-hidden'>
          <PlaygroundChat
            messages={messages}
            generationResults={generationResults}
            onUseGeneratedImage={handleUseGeneratedImage}
            onCopyMessage={handleCopyMessage}
            onRegenerateMessage={handleRegenerateMessage}
            onEditMessage={handleEditMessage}
            onDeleteMessage={handleDeleteMessage}
            isGenerating={isGenerating}
            editingKey={editingMessageKey}
            onCancelEdit={handleEditOpenChange}
            onSaveEdit={(newContent) => applyEdit(newContent, false)}
            onSaveEditAndSubmit={(newContent) => applyEdit(newContent, true)}
          />
        </div>

        <div className='bg-background/80 shrink-0 border-t p-3'>
          <PlaygroundInput
            disabled={isGenerating || isGeneratingMedia}
            mode={mode}
            onModeChange={setMode}
            groups={groups}
            groupValue={config.group}
            isGenerating={isGenerating || isGeneratingMedia}
            isModelLoading={isLoadingModels}
            imageAttachmentRequest={imageAttachmentRequest}
            modelValue={config.model}
            models={modeModels}
            onGroupChange={(value) => updateConfig('group', value)}
            onModelChange={(value) => updateConfig('model', value)}
            onStop={stopGeneration}
            onGenerateImage={handleGenerateImage}
            onGenerateVideo={handleGenerateVideo}
            onSubmit={handleSendMessage}
          />
        </div>
      </div>
    </div>
  )
}
