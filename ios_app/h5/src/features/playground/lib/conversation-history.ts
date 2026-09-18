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
import { MESSAGE_STATUS } from '../constants'
import { getDisplayImageUrl } from './media-url'
import type {
  GenerationResult,
  Message,
  MessageAttachment,
  PlaygroundConversationSyncPayload,
  PlaygroundMode,
  PlaygroundStoredMessage,
  PlaygroundSyncMessage,
} from '../types'

export function getConversationTitle(messages: Message[], fallback: string) {
  const firstUser = messages.find((message) => message.from === 'user')
  const content = firstUser?.versions?.[0]?.content?.trim()
  if (!content) return fallback
  return content.length > 80 ? content.slice(0, 80) : content
}

export function buildConversationSyncPayload({
  messages,
  generationResults,
  model,
  mode,
  title,
}: {
  messages: Message[]
  generationResults: GenerationResult[]
  model: string
  mode: PlaygroundMode
  title: string
}): PlaygroundConversationSyncPayload {
  const textMessages: PlaygroundSyncMessage[] = messages.map((message) => {
    const version = message.versions[0]
    return {
      client_key: message.key,
      role: message.from,
      mode: 'chat',
      model,
      content: version?.content || '',
      attachments: version?.attachments,
      status: message.status,
      error: message.status === MESSAGE_STATUS.ERROR ? version?.content : '',
    }
  })

  const mediaMessages: PlaygroundSyncMessage[] = generationResults.map(
    (result) => ({
      client_key: result.id,
      role: 'assistant',
      mode: result.mode,
      model: result.model,
      content: result.prompt,
      result_payload: result,
      status: result.status,
      error: result.status === 'error' ? result.error : '',
    })
  )

  return {
    title,
    last_model: model,
    last_mode: mode,
    messages: [...mediaMessages, ...textMessages],
  }
}

export function restoreMessages(
  storedMessages: PlaygroundStoredMessage[]
): Message[] {
  return storedMessages
    .filter((message) => message.mode === 'chat')
    .map((message) => ({
      key: message.client_key,
      from: message.role,
      versions: [
        {
          id: `${message.client_key}-version`,
          content: message.content,
          attachments: parsePayload<MessageAttachment[]>(message.attachments),
        },
      ],
      status: normalizeMessageStatus(message.status),
      errorCode: null,
    }))
}

export function restoreGenerationResults(
  storedMessages: PlaygroundStoredMessage[]
): GenerationResult[] {
  return storedMessages
    .filter((message) => message.mode === 'image' || message.mode === 'video')
    .map((message) => parsePayload<GenerationResult>(message.result_payload))
    .map(normalizeGenerationResult)
    .filter((result): result is GenerationResult => Boolean(result))
}

function normalizeGenerationResult(
  result: GenerationResult | undefined
): GenerationResult | undefined {
  if (!result) return undefined
  if (result.mode === 'image') {
    return {
      ...result,
      images: result.images.map((image) => ({
        ...image,
        url: getDisplayImageUrl(image.url),
      })),
    }
  }
  if (!result.videoUrl) return result

  return {
    ...result,
    videoUrl: result.videoUrl.replace(
      /^\/(?:pg|v1)\/videos\/([^/]+)\/content$/,
      '/api/public/videos/$1/content'
    ),
  }
}

function parsePayload<T>(payload: string): T | undefined {
  if (!payload) return undefined
  try {
    return JSON.parse(payload) as T
  } catch {
    return undefined
  }
}

function normalizeMessageStatus(status: string): Message['status'] {
  if (
    status === MESSAGE_STATUS.LOADING ||
    status === MESSAGE_STATUS.STREAMING ||
    status === MESSAGE_STATUS.COMPLETE ||
    status === MESSAGE_STATUS.ERROR
  ) {
    return status
  }
  return undefined
}
