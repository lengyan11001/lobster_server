import { createHash } from 'node:crypto'

import { createOpenAI } from '@ai-sdk/openai'
import { Agent } from '@mastra/core/agent'
import { Mastra } from '@mastra/core/mastra'
import { RequestContext } from '@mastra/core/request-context'
import { registerApiRoute } from '@mastra/core/server'
import { createTool } from '@mastra/core/tools'
import { MCPClient } from '@mastra/mcp'
import { Memory } from '@mastra/memory'
import { PostgresStore } from '@mastra/pg'
import { z } from 'zod'

type DispatchRecord = {
  message_id: string
  status: string
  online: boolean
  installation_id?: string
}

type ChatAttachment = {
  asset_id?: string
  url: string
  name?: string
  media_type?: string
  content_type?: string
  size?: number
}

type LobsterContext = {
  authorization: string
  brand: string
  userId: string
  installationId: string
  parentMessageId: string
  permissionMode: string
  approvalGranted: boolean
  approvalId: string
  dispatches: DispatchRecord[]
}

const backendBase = (process.env.LOBSTER_INTERNAL_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '')
const modelId = process.env.LOBSTER_MASTRA_MODEL || 'openai/gpt-5.6-sol'
const maxConcurrency = Math.max(1, Math.min(12, Number(process.env.LOBSTER_MASTRA_MAX_CONCURRENCY || 4)))

function databaseUrl(): string {
  const raw = process.env.DATABASE_URL || ''
  if (!raw) throw new Error('DATABASE_URL is required for Mastra memory')
  return raw.replace(/^postgresql\+psycopg:/, 'postgresql:')
}

function expectedInternalSecret(): string {
  const configured = process.env.LOBSTER_MASTRA_INTERNAL_SECRET || ''
  if (configured) return configured
  const appSecret = process.env.LOBSTER_SECRET_KEY || process.env.SECRET_KEY || ''
  if (!appSecret) throw new Error('SECRET_KEY is required for Mastra internal authentication')
  return createHash('sha256').update(`${appSecret}:lobster-mastra`).digest('hex')
}

function contextValue<T extends keyof LobsterContext>(context: RequestContext<LobsterContext> | undefined, key: T): LobsterContext[T] {
  if (!context) throw new Error('Missing request context')
  return context.get(key) as LobsterContext[T]
}

async function backendJson(path: string, init: RequestInit, context: RequestContext<LobsterContext> | undefined) {
  const token = contextValue(context, 'authorization')
  const brand = contextValue(context, 'brand')
  const installationId = contextValue(context, 'installationId')
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  headers.set('Content-Type', 'application/json')
  if (brand) headers.set('X-Lobster-Brand', brand)
  if (installationId) headers.set('X-Installation-Id', installationId)
  const response = await fetch(`${backendBase}${path}`, { ...init, headers })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : JSON.stringify(data?.detail || data || {})
    throw new Error(detail || `Backend HTTP ${response.status}`)
  }
  return data
}

const readPersonalMemory = createTool({
  id: 'read_personal_memory',
  description: '读取当前用户在 IP 人设定位中保存的产品资料、FAQ、口播稿和个人记忆。需要按用户资料创作或决策时先调用。',
  inputSchema: z.object({
    query: z.string().optional().describe('要查找的主题或关键词；不填则返回全部可用记忆的摘要内容'),
  }),
  execute: async ({ query }, executionContext) => {
    const context = executionContext?.requestContext as RequestContext<LobsterContext> | undefined
    const installationId = contextValue(context, 'installationId')
    if (!installationId) {
      return { available: false, reason: '当前没有选择 Online 设备，无法确定该设备下的个人记忆。' }
    }
    const data = await backendJson('/api/personal-settings/memory-documents/list', { method: 'GET' }, context)
    const docs = Array.isArray(data?.documents) ? data.documents : []
    const needle = String(query || '').trim().toLowerCase()
    const matched = docs.filter((doc: Record<string, unknown>) => {
      if (!needle) return true
      return [doc.title, doc.filename, doc.notes, doc.content_text]
        .some(value => String(value || '').toLowerCase().includes(needle))
    })
    let totalChars = 0
    const documents = [] as Record<string, unknown>[]
    for (const doc of matched.slice(0, 20)) {
      const content = String(doc.content_text || '')
      if (totalChars >= 60000) break
      const remaining = Math.max(0, 60000 - totalChars)
      const clipped = content.slice(0, remaining)
      totalChars += clipped.length
      documents.push({
        doc_id: doc.doc_id,
        title: doc.title || doc.filename || '个人记忆',
        source: doc.source || 'own',
        notes: doc.notes || '',
        content: clipped,
      })
    }
    return { available: documents.length > 0, count: documents.length, documents }
  },
})

const dispatchOnlineTask = createTool({
  id: 'dispatch_online_task',
  description: '把必须依赖用户电脑、桌面客户端、浏览器登录态或本机文件的任务下发给 Online 客户端执行。',
  inputSchema: z.object({
    task: z.string().min(1).describe('给 Online 的完整、可直接执行的任务描述，必须保留用户原始参数'),
    reason: z.string().min(1).describe('为什么该任务必须在 Online 执行'),
  }),
  execute: async ({ task, reason }, executionContext) => {
    const context = executionContext?.requestContext as RequestContext<LobsterContext> | undefined
    if (contextValue(context, 'permissionMode') !== 'full' && !contextValue(context, 'approvalGranted')) {
      const approval = await backendJson(
        '/api/mastra-chat/approval-request',
        {
          method: 'POST',
          body: JSON.stringify({
            task,
            reason,
            execution_target: 'online',
            parent_message_id: contextValue(context, 'parentMessageId'),
          }),
        },
        context,
      )
      return {
        dispatched: false,
        approval_required: true,
        approval: approval?.approval || null,
        user_hint: '任务尚未下发，正在等待用户确认。',
      }
    }
    const parentMessageId = contextValue(context, 'parentMessageId')
    const result = await backendJson(
      '/api/mastra-chat/online-dispatch',
      {
        method: 'POST',
        body: JSON.stringify({
          task,
          reason,
          parent_message_id: parentMessageId,
          installation_id: contextValue(context, 'installationId'),
          approval_id: contextValue(context, 'approvalId'),
        }),
      },
      context,
    )
    const record: DispatchRecord = {
      message_id: String(result?.message?.id || ''),
      status: String(result?.message?.status || 'pending'),
      online: Boolean(result?.online),
      installation_id: String(result?.message?.installation_id || ''),
    }
    contextValue(context, 'dispatches').push(record)
    return {
      dispatched: true,
      ...record,
      user_hint: record.online
        ? '任务已下发，Online 正在处理。'
        : '任务已下发，但当前 Online 不在线，请提示用户启动 Online 客户端。',
    }
  },
})

const requestTaskApproval = createTool({
  id: 'request_task_approval',
  description: '当当前会话要求执行前确认时，提交清晰的执行方案给用户确认。任何会产生任务、费用、发布、发送、修改或外部操作的动作都必须先调用。',
  inputSchema: z.object({
    task: z.string().min(1).describe('确认后将执行的具体动作、对象和关键参数'),
    reason: z.string().min(1).describe('为什么需要执行，以及会产生什么结果'),
    execution_target: z.enum(['auto', 'server', 'online']).default('auto'),
  }),
  execute: async ({ task, reason, execution_target }, executionContext) => {
    const context = executionContext?.requestContext as RequestContext<LobsterContext> | undefined
    if (contextValue(context, 'permissionMode') === 'full' || contextValue(context, 'approvalGranted')) {
      return { approval_required: false, approved: true }
    }
    const result = await backendJson(
      '/api/mastra-chat/approval-request',
      {
        method: 'POST',
        body: JSON.stringify({
          task,
          reason,
          execution_target,
          parent_message_id: contextValue(context, 'parentMessageId'),
        }),
      },
      context,
    )
    return {
      approval_required: !result?.approved,
      approved: Boolean(result?.approved),
      approval: result?.approval || null,
      user_hint: result?.approved ? '当前会话已授权，可继续执行。' : '请等待用户确认后再执行，不得声称任务已完成。',
    }
  },
})

const getOnlineTaskStatus = createTool({
  id: 'get_online_task_status',
  description: '查询已下发到 Online 的任务状态和最终结果。',
  inputSchema: z.object({ message_id: z.string().min(8) }),
  execute: async ({ message_id }, executionContext) => {
    const context = executionContext?.requestContext as RequestContext<LobsterContext> | undefined
    return backendJson(`/api/mastra-chat/online-tasks/${encodeURIComponent(message_id)}`, { method: 'GET' }, context)
  },
})

const storage = new PostgresStore({
  id: 'lobster-mastra-storage',
  connectionString: databaseUrl(),
  max: Math.max(2, Math.min(8, maxConcurrency + 1)),
  idleTimeoutMillis: 30000,
})

const memory = new Memory({
  storage,
  options: {
    lastMessages: 30,
  },
})

const orchestrator = new Agent({
  id: 'lobster-orchestrator',
  name: 'AI 调度助手',
  instructions: `
你是必火 AI 员工的服务器调度大脑。你的职责是理解用户目标、读取其既有资料、选择现有技能，并给出真实执行结果。

执行规则：
1. 服务器可完成的 LLM、图片、视频、语音、资料查询和云端 API 能力，直接使用现有 MCP 工具执行。
2. 涉及微信、朋友圈、抖音、视频号、阿里询盘浏览器、桌面软件、个人账号登录态、本机文件或其他 GUI 自动化时，必须调用 dispatch_online_task，不要假装服务器已经操作电脑。
3. 下发后根据工具返回的 online 状态明确告诉用户：在线则正在处理；离线则需要启动 Online。不要编造完成结果。
4. 需要产品、个人、人设、FAQ 或历史话术时，先调用 read_personal_memory。不得编造记忆中不存在的参数、数据或业务事实。
5. 只调用当前用户 MCP 返回的能力，权限不足时明确说明缺少哪项权限。所有工具结果、任务编号、素材地址和扣费信息必须以工具原样返回为准。
6. 用户只是咨询时直接回答；用户明确要求执行时才调用工具。不要把同一任务同时交给服务器和 Online 重复执行。
7. 回复使用中文，先给结果和状态，再给必要细节。不要暴露 Mastra、MCP、速推、模型供应商或内部服务名称。
  `.trim(),
  model: ({ requestContext }) => {
    const context = requestContext as RequestContext<LobsterContext>
    const token = contextValue(context, 'authorization')
    const brand = contextValue(context, 'brand')
    const installationId = contextValue(context, 'installationId')
    const provider = createOpenAI({
      baseURL: `${backendBase}/api/sutui-chat`,
      apiKey: token,
      headers: {
        ...(brand ? { 'X-Lobster-Brand': brand } : {}),
        ...(installationId ? { 'X-Installation-Id': installationId } : {}),
      },
    })
    return provider(modelId)
  },
  tools: {
    readPersonalMemory,
    requestTaskApproval,
    dispatchOnlineTask,
    getOnlineTaskStatus,
  },
  memory,
})

let activeRequests = 0
const waiters: Array<() => void> = []

async function acquireSlot() {
  if (activeRequests < maxConcurrency) {
    activeRequests += 1
    return
  }
  await new Promise<void>(resolve => waiters.push(resolve))
}

function releaseSlot() {
  const next = waiters.shift()
  if (next) {
    next()
    return
  }
  activeRequests = Math.max(0, activeRequests - 1)
}

function toolDisplayName(toolName: string, args?: Record<string, unknown>): string {
  const key = String(toolName || '').toLowerCase()
  if (key.includes('readpersonalmemory') || key.includes('read_personal_memory')) return '读取个人记忆'
  if (key.includes('dispatchonlinetask') || key.includes('dispatch_online_task')) return '下发到 Online'
  if (key.includes('getonlinetaskstatus') || key.includes('get_online_task_status')) return '查询 Online 任务'
  if (key.includes('invoke_capability')) {
    const capability = String(args?.capability_id || '')
    if (capability.includes('image')) return '生成或理解图片'
    if (capability.includes('video')) return '生成或理解视频'
    if (capability.includes('audio') || capability.includes('voice') || capability.includes('tts')) return '处理声音'
    return '调用 AI 能力'
  }
  if (key.includes('list_capabilities')) return '检查可用能力'
  if (key.includes('list_assets')) return '查询内容素材'
  if (key.includes('save_asset')) return '保存生成素材'
  if (key.includes('manage_skills')) return '检查技能'
  if (key.includes('publish')) return '提交发布任务'
  return '执行任务'
}

function requestContextFor(body: Record<string, unknown>, dispatches: DispatchRecord[]) {
  const requestContext = new RequestContext<LobsterContext>()
  requestContext.set('authorization', String(body.authorization || '').replace(/^Bearer\s+/i, '').trim())
  requestContext.set('brand', String(body.brand || '').trim())
  requestContext.set('userId', String(body.user_id || '').trim())
  requestContext.set('installationId', String(body.installation_id || '').trim())
  requestContext.set('parentMessageId', String(body.parent_message_id || '').trim())
  requestContext.set('permissionMode', String(body.permission_mode || 'confirm').trim() === 'full' ? 'full' : 'confirm')
  requestContext.set('approvalGranted', Boolean(body.approval_granted))
  requestContext.set('approvalId', String(body.approval_id || '').trim())
  requestContext.set('dispatches', dispatches)
  return requestContext
}

function legacyContextFor(body: Record<string, unknown>) {
  const legacy = Array.isArray(body.legacy_history) ? body.legacy_history : []
  return legacy
    .map(item => {
      const row = item as Record<string, unknown>
      const role = row.role === 'assistant' ? 'assistant' : 'user'
      const content = String(row.content || '').trim()
      return content ? { role, content } : null
    })
    .filter(Boolean) as Array<{ role: 'user' | 'assistant'; content: string }>
}

function attachmentsFor(body: Record<string, unknown>): ChatAttachment[] {
  if (!Array.isArray(body.attachments)) return []
  const attachments: ChatAttachment[] = []
  for (const value of body.attachments) {
    const row = value && typeof value === 'object' ? value as Record<string, unknown> : {}
    const url = String(row.url || '').trim()
    if (!/^https?:\/\//i.test(url)) continue
    attachments.push({
      asset_id: String(row.asset_id || '').trim(),
      url,
      name: String(row.name || '素材').trim(),
      media_type: String(row.media_type || 'file').trim().toLowerCase(),
      content_type: String(row.content_type || '').trim(),
      size: Number(row.size || 0),
    })
    if (attachments.length >= 8) break
  }
  return attachments
}

function chatInputFor(body: Record<string, unknown>, message: string) {
  const attachments = attachmentsFor(body)
  const canExecute = String(body.permission_mode || '') === 'full' || Boolean(body.approval_granted)
  const permissionNotice = canExecute
    ? '【执行权限】本轮已经获得执行授权，可以调用工具完成任务。不要再次请求确认。'
    : '【执行权限】本会话要求执行前确认。你可以回答问题、读取个人记忆和制定方案；如需调用技能、生成内容、产生费用、发布、发送、修改数据或下发 Online，必须先调用 request_task_approval，然后等待用户确认。未确认前不得声称已经执行。'
  const baseMessage = `${permissionNotice}\n\n${message || ''}`.trim()
  if (!attachments.length) return baseMessage
  const manifest = attachments.map((item, index) => (
    `${index + 1}. ${item.name || '素材'} [${item.media_type || 'file'}] ${item.url}`
  )).join('\n')
  const text = `${permissionNotice}\n\n${message || '请分析并使用我提供的素材。'}\n\n本轮附加素材：\n${manifest}\n\n` +
    '请把这些素材视为用户本轮的真实输入。图片可直接理解；视频、音频或文档需要处理时，请调用已有能力，不要忽略素材，也不要编造素材内容。'
  const content: Array<
    { type: 'text'; text: string } |
    { type: 'image'; image: URL; mimeType?: string }
  > = [{ type: 'text', text }]
  for (const item of attachments) {
    if (item.media_type !== 'image') continue
    content.push({
      type: 'image',
      image: new URL(item.url),
      ...(item.content_type ? { mimeType: item.content_type } : {}),
    })
  }
  return [{ role: 'user' as const, content }]
}

function validateChatBody(body: Record<string, unknown> | null) {
  const message = String(body?.message || '').trim()
  const token = String(body?.authorization || '').replace(/^Bearer\s+/i, '').trim()
  const userId = String(body?.user_id || '').trim()
  const threadId = String(body?.thread_id || '').trim()
  const resourceId = String(body?.resource_id || '').trim()
  const parentMessageId = String(body?.parent_message_id || '').trim()
  return {
    ok: Boolean(message && token && userId && threadId && resourceId && parentMessageId),
    message,
    token,
    userId,
    threadId,
    resourceId,
    parentMessageId,
  }
}

function mcpClientFor(body: Record<string, unknown>, token: string, parentMessageId: string) {
  const brand = String(body.brand || '').trim()
  const userId = String(body.user_id || '').trim()
  const installationId = String(body.installation_id || '').trim()
  return new MCPClient({
    id: `lobster-${brand || 'bihuo'}-${userId}-${parentMessageId}`,
    servers: {
      lobster: {
        url: new URL(`${backendBase}/mcp-gateway`),
        requestInit: {
          headers: {
            Authorization: `Bearer ${token}`,
            'X-User-Authorization': `Bearer ${token}`,
            ...(brand ? { 'X-Lobster-Brand': brand } : {}),
            ...(installationId ? { 'X-Installation-Id': installationId } : {}),
          },
        },
      },
    },
  })
}

function executionAllowed(body: Record<string, unknown>): boolean {
  return String(body.permission_mode || '').trim() === 'full' || Boolean(body.approval_granted)
}

const internalChatRoute = registerApiRoute('/internal/chat', {
  method: 'POST',
  requiresAuth: false,
  handler: async c => {
    if (c.req.header('X-Lobster-Mastra-Secret') !== expectedInternalSecret()) {
      return c.json({ ok: false, error: 'forbidden' }, 403)
    }
    const body = await c.req.json().catch(() => null) as Record<string, unknown> | null
    const validated = validateChatBody(body)
    const message = validated.message
    const token = validated.token
    const brand = String(body?.brand || '').trim()
    const userId = validated.userId
    const threadId = validated.threadId
    const resourceId = validated.resourceId
    const installationId = String(body?.installation_id || '').trim()
    const parentMessageId = validated.parentMessageId
    if (!validated.ok) {
      return c.json({ ok: false, error: 'missing required chat context' }, 400)
    }

    let acquired = false
    let mcp: MCPClient | null = null
    const dispatches: DispatchRecord[] = []
    const requestContext = requestContextFor(body || {}, dispatches)

    const toolSteps: Array<Record<string, unknown>> = []
    try {
      await acquireSlot()
      acquired = true
      const allowExecution = executionAllowed(body || {})
      if (allowExecution) mcp = mcpClientFor(body || {}, token, parentMessageId)
      const context = legacyContextFor(body || {})
      const result = await orchestrator.generate(chatInputFor(body || {}, message), {
        memory: { thread: threadId, resource: resourceId },
        requestContext,
        toolsets: mcp ? await mcp.listToolsets() : {},
        context,
        maxSteps: 12,
        modelSettings: { maxOutputTokens: 4096, temperature: 0.2 },
        onStepFinish: step => {
          const calls = (step.toolCalls || []) as unknown as Array<Record<string, unknown>>
          toolSteps.push({
            finishReason: step.finishReason,
            toolCalls: calls.map(call => ({ toolName: call.toolName, toolCallId: call.toolCallId })),
          })
        },
      })
      return c.json({
        ok: true,
        reply: result.text || '',
        dispatches,
        usage: result.usage || null,
        steps: toolSteps,
      })
    } catch (error) {
      const messageText = error instanceof Error ? error.message : String(error)
      return c.json({ ok: false, error: messageText, dispatches }, 502)
    } finally {
      if (mcp) await mcp.disconnect().catch(() => undefined)
      if (acquired) releaseSlot()
    }
  },
})

const internalChatStreamRoute = registerApiRoute('/internal/chat/stream', {
  method: 'POST',
  requiresAuth: false,
  handler: async c => {
    if (c.req.header('X-Lobster-Mastra-Secret') !== expectedInternalSecret()) {
      return c.json({ ok: false, error: 'forbidden' }, 403)
    }
    const body = await c.req.json().catch(() => null) as Record<string, unknown> | null
    const validated = validateChatBody(body)
    if (!validated.ok || !body) {
      return c.json({ ok: false, error: 'missing required chat context' }, 400)
    }

    const encoder = new TextEncoder()
    const responseStream = new ReadableStream<Uint8Array>({
      start: async controller => {
        const write = (event: Record<string, unknown>) => {
          controller.enqueue(encoder.encode(`${JSON.stringify(event)}\n`))
        }
        let mcp: MCPClient | null = null
        let acquired = false
        const dispatches: DispatchRecord[] = []
        const activeToolNames = new Map<string, string>()
        try {
          write({ type: 'thinking', text: '正在理解你的需求并检查可用能力...' })
          await acquireSlot()
          acquired = true
          const allowExecution = executionAllowed(body)
          if (allowExecution) mcp = mcpClientFor(body, validated.token, validated.parentMessageId)
          const requestContext = requestContextFor(body, dispatches)
          const output = await orchestrator.stream(chatInputFor(body, validated.message), {
            memory: { thread: validated.threadId, resource: validated.resourceId },
            requestContext,
            toolsets: mcp ? await mcp.listToolsets() : {},
            context: legacyContextFor(body),
            maxSteps: 12,
            modelSettings: { maxOutputTokens: 4096, temperature: 0.2 },
          })

          for await (const chunk of output.fullStream) {
            const item = chunk as unknown as { type: string; payload?: Record<string, unknown> }
            const payload = item.payload || {}
            if (item.type === 'text-delta') {
              const text = String(payload.text || '')
              if (text) write({ type: 'delta', text })
              continue
            }
            if (item.type === 'tool-call') {
              const callId = String(payload.toolCallId || '')
              const rawName = String(payload.toolName || '')
              const args = payload.args && typeof payload.args === 'object' ? payload.args as Record<string, unknown> : undefined
              const displayName = toolDisplayName(rawName, args)
              if (callId) activeToolNames.set(callId, displayName)
              write({ type: 'tool_start', name: displayName })
              continue
            }
            if (item.type === 'tool-result') {
              const callId = String(payload.toolCallId || '')
              const rawName = String(payload.toolName || '')
              const displayName = activeToolNames.get(callId) || toolDisplayName(rawName)
              write({ type: 'tool_end', name: displayName })
              continue
            }
            if (item.type === 'error') {
              const rawError = payload.error
              const errorText = rawError instanceof Error ? rawError.message : String(rawError || 'AI 调度失败')
              throw new Error(errorText)
            }
          }
          const reply = await output.text
          const usage = await output.usage
          write({ type: 'final', reply: reply || '', dispatches, usage: usage || null })
        } catch (error) {
          const messageText = error instanceof Error ? error.message : String(error)
          write({ type: 'error', error: messageText, dispatches })
        } finally {
          if (mcp) await mcp.disconnect().catch(() => undefined)
          if (acquired) releaseSlot()
          controller.close()
        }
      },
    })
    return new Response(responseStream, {
      headers: {
        'Content-Type': 'application/x-ndjson; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
      },
    })
  },
})

export const mastra = new Mastra({
  storage,
  agents: { orchestrator },
  server: {
    host: '127.0.0.1',
    port: Number(process.env.LOBSTER_MASTRA_PORT || 4111),
    apiRoutes: [internalChatRoute, internalChatStreamRoute],
  },
})
