type CapabilityDefinition = Record<string, unknown>

type CapabilitySchema = {
  properties?: Record<string, unknown>
  required?: unknown[]
}

export type CapabilitySearchRow = {
  capability_id: string
  description: string
  execution_target: string
  action: string
  keywords: string[]
  parameters: string[]
  required_parameters: string[]
  parameter_schema: Record<string, unknown>
}

export type CapabilitySearchResult = {
  matched_count: number
  available_count: number
  capabilities: CapabilitySearchRow[]
}

const CAPABILITY_ALIASES: Record<string, string[]> = {
  'image.generate': ['图片', '生成图片', '出图', '作图', '做图', '画图', '海报', '封面', '配图', '照片', '商品图', '文生图', '图生图'],
  'video.generate': ['视频', '生成视频', '出视频', '短视频', '成片', '文生视频', '图生视频', '音乐视频'],
  'task.get_result': ['任务结果', '查询结果', '查看结果', '生成结果'],
}

const COMMON_INTENT_HINTS = [
  '图片',
  '生成图片',
  '出图',
  '作图',
  '做图',
  '画图',
  '海报',
  '封面',
  '配图',
  '照片',
  '商品图',
  '视频',
  '生成视频',
  '短视频',
  '成片',
  '音乐视频',
  'ppt',
  '幻灯片',
  'word',
  '文档',
  '转写',
  '录音',
  '语音',
  '音频',
  '微信',
  '朋友圈',
  '抖音',
  '视频号',
  '公众号',
  '素材',
  '记忆',
  '人设',
  '拉群',
  '获客',
  '同行',
  '剪辑',
  '数字人',
]

function asObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function capabilityAliases(capabilityId: string): string[] {
  const aliases = [...(CAPABILITY_ALIASES[capabilityId] || [])]
  if (capabilityId.includes('image')) aliases.push(...CAPABILITY_ALIASES['image.generate'])
  if (capabilityId.includes('video')) aliases.push(...CAPABILITY_ALIASES['video.generate'])
  return Array.from(new Set(aliases.map(item => item.trim().toLowerCase()).filter(Boolean)))
}

export function capabilitySearchTerms(query: string): string[] {
  const needle = String(query || '').trim().toLowerCase()
  const terms = new Set(
    needle
      .split(/[\s,，、。.!！?？;；:：/|]+/)
      .map(item => item.trim())
      .filter(Boolean),
  )
  for (const hint of COMMON_INTENT_HINTS) {
    const normalized = hint.toLowerCase()
    if (needle.includes(normalized)) terms.add(normalized)
  }
  if (/图/.test(needle) && /(做|作|画|出|生成|制作|设计|一张|一个|照片|海报|封面|配图|商品)/.test(needle)) {
    terms.add('图片')
  }
  return Array.from(terms)
}

function parameterSchemaFor(schema: unknown): {
  properties: string[]
  required: string[]
  parameterSchema: Record<string, unknown>
} {
  const schemaObj = asObject(schema) as CapabilitySchema
  const propertyDefinitions = asObject(schemaObj.properties)
  const properties = Object.keys(propertyDefinitions).slice(0, 12)
  const required = Array.isArray(schemaObj.required)
    ? schemaObj.required.map(value => String(value)).slice(0, 12)
    : []
  const parameterSchema = Object.fromEntries(properties.map(name => {
    const rule = asObject(propertyDefinitions[name])
    return [name, {
      type: String(rule.type || 'unknown'),
      required: required.includes(name),
      ...(Object.prototype.hasOwnProperty.call(rule, 'default') ? { default: rule.default } : {}),
      ...(Array.isArray(rule.enum) ? { enum: rule.enum.slice(0, 20) } : {}),
      ...(rule.minimum !== undefined ? { minimum: rule.minimum } : {}),
      ...(rule.maximum !== undefined ? { maximum: rule.maximum } : {}),
      ...(rule.maxItems !== undefined ? { max_items: rule.maxItems } : {}),
      ...(rule.description ? { description: String(rule.description).slice(0, 200) } : {}),
    }]
  }))
  return { properties, required, parameterSchema }
}

export function searchCapabilities(
  catalog: Record<string, CapabilityDefinition>,
  query = '',
  limit = 24,
): CapabilitySearchResult {
  const terms = capabilitySearchTerms(query)
  const rows = Object.entries(catalog).map(([capabilityId, definition]) => {
    const description = String(definition.description || definition.name || '')
    const keywords = Array.isArray(definition.keywords)
      ? definition.keywords.map(value => String(value || '').trim()).filter(Boolean).slice(0, 20)
      : []
    const aliases = capabilityAliases(capabilityId)
    const action = String(definition.action || '')
    const executionTarget = String(definition.execution_target || 'server')
    const schema = definition.arg_schema || definition.inputSchema || definition.input_schema
    const { properties, required, parameterSchema } = parameterSchemaFor(schema)
    const haystack = `${capabilityId} ${description} ${action} ${keywords.join(' ')} ${aliases.join(' ')}`.toLowerCase()
    const score = terms.reduce((total, term) => total + (haystack.includes(term) ? Math.max(1, Math.min(3, term.length)) : 0), 0)
    return {
      capability_id: capabilityId,
      description: description.slice(0, 400),
      execution_target: executionTarget,
      action,
      keywords,
      parameters: properties,
      required_parameters: required,
      parameter_schema: parameterSchema,
      score,
    }
  })
  const matched = (terms.length ? rows.filter(row => row.score > 0) : rows)
    .sort((a, b) => b.score - a.score || a.capability_id.localeCompare(b.capability_id))
    .slice(0, limit)
    .map(({ score: _score, ...row }) => row)
  return {
    matched_count: matched.length,
    available_count: rows.length,
    capabilities: matched,
  }
}
