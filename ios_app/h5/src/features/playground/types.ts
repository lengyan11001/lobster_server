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
// Message types
export type MessageRole = 'user' | 'assistant' | 'system'

export type MessageStatus = 'loading' | 'streaming' | 'complete' | 'error'

export interface MessageVersion {
  id: string
  content: string
  attachments?: MessageAttachment[]
}

export interface MessageAttachment {
  id: string
  name: string
  type: string
  url: string
  size: number
}

export interface Message {
  key: string
  from: MessageRole
  versions: MessageVersion[]
  sources?: { href: string; title: string }[]
  reasoning?: {
    content: string
    duration: number
  }
  isReasoningStreaming?: boolean
  isReasoningComplete?: boolean
  isContentComplete?: boolean
  status?: MessageStatus
  errorCode?: string | null
}

// API payload types
export interface ChatCompletionMessage {
  role: MessageRole
  content: string | ContentPart[]
}

export interface ContentPart {
  type: 'text' | 'image_url' | 'file'
  text?: string
  image_url?: {
    url: string
    detail?: string
  }
  file?: {
    url: string
    filename?: string
  }
}

export interface ChatCompletionRequest {
  model: string
  group?: string
  messages: ChatCompletionMessage[]
  stream: boolean
  temperature?: number
  top_p?: number
  max_tokens?: number
  frequency_penalty?: number
  presence_penalty?: number
  seed?: number
}

export interface ChatCompletionChunk {
  id: string
  object: string
  created: number
  model: string
  choices: Array<{
    index: number
    delta: {
      role?: MessageRole
      content?: string
      reasoning_content?: string
    }
    finish_reason: string | null
  }>
}

export interface ChatCompletionResponse {
  id: string
  object: string
  created: number
  model: string
  choices: Array<{
    index: number
    message: {
      role: MessageRole
      content: string
      reasoning_content?: string
    }
    finish_reason: string
  }>
  usage?: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
}

export type PlaygroundMode = 'chat' | 'image' | 'video'

export interface ImageGenerationRequest {
  model: string
  group?: string
  prompt: string
  size?: string
  quality?: string
  resolution?: string
  aspect_ratio?: string
  image_size?: string
  n?: number
  response_format?: string
  images?: string[]
  image?: string | string[]
  input_fidelity?: string
}

export interface ImageGenerationResponse {
  created?: number
  data?: Array<{
    url?: string
    b64_json?: string
    revised_prompt?: string
  }>
  error?: {
    message?: string
    code?: string
  }
}

export interface ImageTaskResponse {
  id?: string
  task_id?: string
  status?: string
  progress?: number | string
  image_url?: string
  result_url?: string
  url?: string
  output?: {
    url?: string
  }
  result?: {
    url?: string
  }
  data?: ImageGenerationResponse
  error?: {
    message?: string
    code?: string
  }
}

export interface VideoGenerationRequest {
  model: string
  group?: string
  prompt: string
  aspect_ratio?: string
  duration?: number
  seconds?: string
  size?: string
  resolution?: string
  metadata?: Record<string, unknown>
  image?: string
  image_urls?: string[]
  reference_images?: string[]
  generate_audio?: boolean
}

export interface VideoGenerationResponse {
  id?: string
  task_id?: string
  status?: string
  progress?: number | string
  result_url?: string
  video_url?: string
  url?: string
  data?: VideoGenerationResponse
  error?: {
    message?: string
    code?: string
  }
}

export type GenerationResult =
  | {
      id: string
      mode: 'image'
      prompt: string
      status: 'loading' | 'complete' | 'error'
      model: string
      createdAt: number
      images: Array<{
        url: string
        revisedPrompt?: string
      }>
      error?: string
    }
  | {
      id: string
      mode: 'video'
      prompt: string
      status: 'loading' | 'processing' | 'complete' | 'error'
      model: string
      createdAt: number
      taskId?: string
      progress?: number
      videoUrl?: string
      error?: string
    }

export interface PlaygroundConversation {
  id: number
  user_id: number
  title: string
  last_model: string
  last_mode: PlaygroundMode | string
  created_time: number
  updated_time: number
}

export interface PlaygroundStoredMessage {
  id: number
  conversation_id: number
  user_id: number
  client_key: string
  role: MessageRole
  mode: PlaygroundMode | string
  model: string
  content: string
  attachments: string
  result_payload: string
  status: string
  error: string
  sort_order: number
  created_time: number
  updated_time: number
}

export interface PlaygroundConversationDetail {
  conversation: PlaygroundConversation
  messages: PlaygroundStoredMessage[]
}

export interface PlaygroundSyncMessage {
  client_key: string
  role: MessageRole
  mode: PlaygroundMode
  model: string
  content: string
  attachments?: MessageAttachment[]
  result_payload?: GenerationResult
  status?: string
  error?: string
}

export interface PlaygroundConversationSyncPayload {
  title: string
  last_model: string
  last_mode: PlaygroundMode
  messages: PlaygroundSyncMessage[]
}

// Configuration types
export interface PlaygroundConfig {
  model: string
  group: string
  temperature: number
  top_p: number
  max_tokens: number
  frequency_penalty: number
  presence_penalty: number
  seed: number | null
  stream: boolean
}

export interface ParameterEnabled {
  temperature: boolean
  top_p: boolean
  max_tokens: boolean
  frequency_penalty: boolean
  presence_penalty: boolean
  seed: boolean
}

// Model and group options
export interface ModelOption {
  label: string
  value: string
}

export interface GroupOption {
  label: string
  value: string
  ratio: number
  desc?: string
}
