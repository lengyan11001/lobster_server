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
import type {
  ChatCompletionRequest,
  Message,
  PlaygroundConfig,
  ParameterEnabled,
  ContentPart,
  ChatCompletionMessage,
} from '../types'
import { formatMessageForAPI, isValidMessage } from './message-utils'

/**
 * Build API request payload from messages and config
 */
export function buildChatCompletionPayload(
  messages: Message[],
  config: PlaygroundConfig,
  parameterEnabled: ParameterEnabled
): ChatCompletionRequest {
  // Filter and format valid messages
  const processedMessages = messages
    .filter(isValidMessage)
    .map(formatMessageForAPI)

  const payload: ChatCompletionRequest = {
    model: config.model,
    group: config.group,
    messages: processedMessages,
    stream: config.stream,
  }

  // Add enabled parameters
  const parameterKeys: Array<keyof ParameterEnabled> = [
    'temperature',
    'top_p',
    'max_tokens',
    'frequency_penalty',
    'presence_penalty',
    'seed',
  ]

  parameterKeys.forEach((key) => {
    if (parameterEnabled[key]) {
      const value = config[key as keyof PlaygroundConfig]
      if (value !== undefined && value !== null) {
        ;(payload as unknown as Record<string, unknown>)[key] = value
      }
    }
  })

  return payload
}

/**
 * Build message with attachments (multimodal)
 */
export function buildMessageWithAttachments(
  text: string,
  attachments?: Array<{
    id: string
    name: string
    type: string
    url: string
    size: number
  }>
): ChatCompletionMessage {
  // If no attachments, return simple text message
  if (!attachments || attachments.length === 0) {
    return {
      role: 'user',
      content: text,
    }
  }

  // Build content parts
  const contentParts: ContentPart[] = [
    {
      type: 'text',
      text: text,
    },
  ]

  // Add attachments
  for (const attachment of attachments) {
    if (attachment.type.startsWith('image/')) {
      contentParts.push({
        type: 'image_url',
        image_url: {
          url: attachment.url,
        },
      })
    } else {
      // For non-image files
      contentParts.push({
        type: 'file',
        file: {
          url: attachment.url,
          filename: attachment.name,
        },
      })
    }
  }

  return {
    role: 'user',
    content: contentParts,
  }
}

