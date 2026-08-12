import test from 'node:test'
import assert from 'node:assert/strict'

import { searchCapabilities } from './capability-search.js'

test('matches image generation for a short Chinese image request', () => {
  const result = searchCapabilities({
    'image.generate': {
      description: '生成图片（文生图/图生图）。速推统一 generate 入口，返回 task_id 后用 task.get_result 取结果。',
      enabled: true,
      arg_schema: {
        type: 'object',
        properties: { prompt: { type: 'string', description: '生成提示词' } },
        required: ['prompt'],
      },
    },
    'video.generate': {
      description: '生成视频（文生视频/图生视频）。',
      enabled: true,
    },
  }, '一个纸巾的图片。')

  assert.equal(result.matched_count >= 1, true)
  assert.equal(result.capabilities[0]?.capability_id, 'image.generate')
})

test('matches image generation aliases that are not in the catalog description', () => {
  const result = searchCapabilities({
    'image.generate': {
      description: '生成图片（文生图/图生图）。',
      enabled: true,
    },
  }, '做一张开业海报')

  assert.equal(result.capabilities[0]?.capability_id, 'image.generate')
})
