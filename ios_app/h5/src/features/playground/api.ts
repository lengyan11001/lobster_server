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
import { api } from '@/lib/api'
import { API_ENDPOINTS } from './constants'
import type {
  ChatCompletionRequest,
  ChatCompletionResponse,
  ImageGenerationRequest,
  ImageGenerationResponse,
  ImageTaskResponse,
  ModelOption,
  GroupOption,
  PlaygroundConversation,
  PlaygroundConversationDetail,
  PlaygroundConversationSyncPayload,
  VideoGenerationRequest,
  VideoGenerationResponse,
} from './types'

/**
 * Send chat completion request (non-streaming)
 */
export async function sendChatCompletion(
  payload: ChatCompletionRequest
): Promise<ChatCompletionResponse> {
  const res = await api.post(API_ENDPOINTS.CHAT_COMPLETIONS, payload, {
    skipErrorHandler: true,
  } as Record<string, unknown>)
  return res.data
}

export async function generateImage(
  payload: ImageGenerationRequest
): Promise<ImageGenerationResponse> {
  const res = await api.post(API_ENDPOINTS.IMAGE_GENERATIONS, payload, {
    skipErrorHandler: true,
  } as Record<string, unknown>)
  return res.data
}

export async function submitImageTask(
  payload: ImageGenerationRequest
): Promise<ImageTaskResponse> {
  const res = await api.post(API_ENDPOINTS.IMAGE_GENERATION_TASKS, payload, {
    skipErrorHandler: true,
  } as Record<string, unknown>)
  return res.data
}

export async function getImageTask(taskId: string): Promise<ImageTaskResponse> {
  const res = await api.get(`${API_ENDPOINTS.IMAGE_TASKS}/${taskId}`, {
    skipErrorHandler: true,
  } as Record<string, unknown>)
  return res.data
}

export async function submitVideo(
  payload: VideoGenerationRequest
): Promise<VideoGenerationResponse> {
  const res = await api.post(API_ENDPOINTS.VIDEO_GENERATIONS, payload, {
    skipErrorHandler: true,
  } as Record<string, unknown>)
  return res.data
}

export async function getVideoTask(
  taskId: string
): Promise<VideoGenerationResponse> {
  const res = await api.get(`${API_ENDPOINTS.VIDEO_GENERATIONS}/${taskId}`, {
    skipErrorHandler: true,
  } as Record<string, unknown>)
  return res.data
}

function unwrapBusinessData<T>(data: { success?: boolean; data?: T }): T {
  return data.data as T
}

export async function listPlaygroundConversations(): Promise<{
  items: PlaygroundConversation[]
  total: number
}> {
  const res = await api.get(API_ENDPOINTS.PLAYGROUND_CONVERSATIONS, {
    params: { p: 1, size: 100 },
  })
  return unwrapBusinessData(res.data) || { items: [], total: 0 }
}

export async function createPlaygroundConversation(payload: {
  title: string
  last_model?: string
  last_mode?: string
}): Promise<PlaygroundConversation> {
  const res = await api.post(API_ENDPOINTS.PLAYGROUND_CONVERSATIONS, payload)
  return unwrapBusinessData(res.data)
}

export async function getPlaygroundConversation(
  id: number
): Promise<PlaygroundConversationDetail> {
  const res = await api.get(`${API_ENDPOINTS.PLAYGROUND_CONVERSATIONS}/${id}`)
  return unwrapBusinessData(res.data)
}

export async function updatePlaygroundConversation(
  id: number,
  payload: Partial<{
    title: string
    last_model: string
    last_mode: string
  }>
): Promise<void> {
  await api.patch(`${API_ENDPOINTS.PLAYGROUND_CONVERSATIONS}/${id}`, payload)
}

export async function deletePlaygroundConversation(id: number): Promise<void> {
  await api.delete(`${API_ENDPOINTS.PLAYGROUND_CONVERSATIONS}/${id}`)
}

export async function syncPlaygroundConversation(
  id: number,
  payload: PlaygroundConversationSyncPayload
): Promise<void> {
  await api.put(
    `${API_ENDPOINTS.PLAYGROUND_CONVERSATIONS}/${id}/sync`,
    payload,
    { skipErrorHandler: true } as Record<string, unknown>
  )
}

/**
 * Get user available models
 */
export async function getUserModels(): Promise<ModelOption[]> {
  const res = await api.get(API_ENDPOINTS.USER_MODELS)
  const { data } = res

  if (!data.success || !Array.isArray(data.data)) {
    return []
  }

  return data.data.map((model: string) => ({
    label: model,
    value: model,
  }))
}

/**
 * Get user group models (models organized by group)
 */
export async function getUserGroupModels(): Promise<Record<string, string[]>> {
  const res = await api.get(API_ENDPOINTS.USER_GROUP_MODELS)
  const { data } = res

  if (!data.success || !data.data) {
    return {}
  }

  return data.data as Record<string, string[]>
}

/**
 * Get user groups
 */
export async function getUserGroups(): Promise<GroupOption[]> {
  const res = await api.get(API_ENDPOINTS.USER_GROUPS)
  const { data } = res

  if (!data.success || !data.data) {
    return []
  }

  const groupData = data.data as Record<string, { desc: string; ratio: number }>

  // label is for button display (name only); desc is for dropdown content
  return Object.entries(groupData).map(([group, info]) => ({
    label: group,
    value: group,
    ratio: info.ratio,
    desc: info.desc,
  }))
}
