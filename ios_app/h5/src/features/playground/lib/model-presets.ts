export type SelectOption = {
  value: string
  label: string
}

export type ImageModelPreset = {
  sizeOptions: SelectOption[]
  qualityOptions: SelectOption[]
  defaultSize: string
  defaultQuality: string
  includeAspectRatio?: boolean
  includeImageSize?: boolean
  includeResolution?: boolean
}

export type VideoModelPreset = {
  durationOptions: SelectOption[]
  sizeOptions: SelectOption[]
  aspectRatioOptions: SelectOption[]
  defaultDuration: string
  defaultSize: string
  defaultAspectRatio: string
}

function option(value: string, label = value): SelectOption {
  return { value, label }
}

const IMAGE_RATIO_OPTIONS: SelectOption[] = [
  option('1:1'),
  option('16:9'),
  option('9:16'),
  option('4:3'),
  option('3:4'),
  option('3:2'),
  option('2:3'),
  option('21:9'),
]

const EXTENDED_IMAGE_RATIO_OPTIONS: SelectOption[] = [
  ...IMAGE_RATIO_OPTIONS,
  option('9:21'),
  option('5:4'),
  option('4:5'),
  option('2:1'),
  option('1:2'),
  option('5:3'),
  option('3:5'),
  option('16:10'),
  option('10:16'),
  option('7:3'),
  option('3:7'),
  option('4:1'),
  option('1:4'),
  option('8:1'),
  option('1:8'),
]

const IMAGE_PIXEL_SIZE_OPTIONS: SelectOption[] = [
  option('auto', 'Auto'),
  option('256x256', '1:1 256x256'),
  option('512x512', '1:1 512x512'),
  option('1024x1024', '1:1 1024x1024'),
  option('1536x1024', '3:2 1536x1024'),
  option('1024x1536', '2:3 1024x1536'),
  option('1792x1024', '16:9 1792x1024'),
  option('1024x1792', '9:16 1024x1792'),
  option('1280x720', '16:9 1280x720'),
  option('720x1280', '9:16 720x1280'),
  option('1280x960', '4:3 1280x960'),
  option('960x1280', '3:4 960x1280'),
  option('1152x864', '4:3 1152x864'),
  option('864x1152', '3:4 864x1152'),
  option('1248x832', '3:2 1248x832'),
  option('832x1248', '2:3 832x1248'),
  option('1792x768', '21:9 1792x768'),
  option('768x1792', '9:21 768x1792'),
  option('1344x576', '21:9 1344x576'),
]

const IMAGE_TIER_OPTIONS: SelectOption[] = [
  option('1K'),
  option('2K'),
  option('4K'),
]

const IMAGE_QUALITY_OPTIONS: SelectOption[] = [
  option('auto', 'Auto'),
  option('standard', 'Standard'),
  option('hd', 'HD'),
]

const GPT_IMAGE_QUALITY_OPTIONS: SelectOption[] = [
  option('auto', 'Auto'),
  option('low', 'Low'),
  option('medium', 'Medium'),
  option('high', 'High'),
]

const DALL_E_2_SIZE_OPTIONS: SelectOption[] = [
  option('256x256', '1:1 256x256'),
  option('512x512', '1:1 512x512'),
  option('1024x1024', '1:1 1024x1024'),
]

const DALL_E_3_SIZE_OPTIONS: SelectOption[] = [
  option('1024x1024', '1:1 1024x1024'),
  option('1792x1024', '16:9 1792x1024'),
  option('1024x1792', '9:16 1024x1792'),
]

const VIDEO_RATIO_OPTIONS: SelectOption[] = [option('16:9'), option('9:16')]

const VIDEO_LANDSCAPE_PORTRAIT_OPTIONS: SelectOption[] = [
  option('16:9'),
  option('9:16'),
]

const DEFAULT_IMAGE_PRESET: ImageModelPreset = {
  sizeOptions: IMAGE_PIXEL_SIZE_OPTIONS,
  qualityOptions: IMAGE_QUALITY_OPTIONS,
  defaultSize: '1024x1024',
  defaultQuality: 'auto',
}

const OPENAI_IMAGE_PRESET: ImageModelPreset = {
  sizeOptions: [
    option('auto', 'Auto'),
    option('1024x1024', '1:1 1024x1024'),
    option('1536x1024', '3:2 1536x1024'),
    option('1024x1536', '2:3 1024x1536'),
  ],
  qualityOptions: GPT_IMAGE_QUALITY_OPTIONS,
  defaultSize: '1024x1024',
  defaultQuality: 'auto',
}

const DALL_E_2_PRESET: ImageModelPreset = {
  sizeOptions: DALL_E_2_SIZE_OPTIONS,
  qualityOptions: [option('standard', 'Standard')],
  defaultSize: '1024x1024',
  defaultQuality: 'standard',
}

const DALL_E_3_PRESET: ImageModelPreset = {
  sizeOptions: DALL_E_3_SIZE_OPTIONS,
  qualityOptions: [option('standard', 'Standard'), option('hd', 'HD')],
  defaultSize: '1024x1024',
  defaultQuality: 'standard',
}

const ASPECT_RATIO_IMAGE_PRESET: ImageModelPreset = {
  sizeOptions: IMAGE_RATIO_OPTIONS,
  qualityOptions: IMAGE_QUALITY_OPTIONS,
  defaultSize: '1:1',
  defaultQuality: 'auto',
  includeAspectRatio: true,
}

const GPT_IMAGE_2_PRESET: ImageModelPreset = {
  sizeOptions: EXTENDED_IMAGE_RATIO_OPTIONS,
  qualityOptions: IMAGE_TIER_OPTIONS,
  defaultSize: '1:1',
  defaultQuality: '1K',
  includeAspectRatio: true,
  includeImageSize: true,
  includeResolution: true,
}

const GEMINI_IMAGE_PRESET: ImageModelPreset = {
  sizeOptions: EXTENDED_IMAGE_RATIO_OPTIONS,
  qualityOptions: IMAGE_TIER_OPTIONS,
  defaultSize: '1:1',
  defaultQuality: '1K',
  includeAspectRatio: true,
  includeImageSize: true,
}

const BANANA_IMAGE_PRESET: ImageModelPreset = {
  sizeOptions: [...EXTENDED_IMAGE_RATIO_OPTIONS, option('auto', 'Auto')],
  qualityOptions: IMAGE_TIER_OPTIONS,
  defaultSize: '1:1',
  defaultQuality: '1K',
  includeAspectRatio: true,
  includeImageSize: true,
  includeResolution: true,
}

const DEFAULT_VIDEO_PRESET: VideoModelPreset = {
  durationOptions: ['5', '6', '8', '10', '15'].map((value) => ({
    value,
    label: `${value}s`,
  })),
  sizeOptions: [option('480p'), option('720p'), option('1080p')],
  aspectRatioOptions: VIDEO_RATIO_OPTIONS,
  defaultDuration: '10',
  defaultSize: '720p',
  defaultAspectRatio: '9:16',
}

const GROK_15_VIDEO_PRESET: VideoModelPreset = {
  durationOptions: ['5', '10', '15'].map((value) => ({
    value,
    label: `${value}s`,
  })),
  sizeOptions: [option('480p'), option('720p'), option('1080p')],
  aspectRatioOptions: VIDEO_LANDSCAPE_PORTRAIT_OPTIONS,
  defaultDuration: '10',
  defaultSize: '480p',
  defaultAspectRatio: '9:16',
}

const GROK_10_VIDEO_PRESET: VideoModelPreset = {
  ...GROK_15_VIDEO_PRESET,
  durationOptions: ['5', '10'].map((value) => ({
    value,
    label: `${value}s`,
  })),
  defaultDuration: '10',
}

const VEO31_VIDEO_PRESET: VideoModelPreset = {
  durationOptions: ['4', '6', '8'].map((value) => ({
    value,
    label: `${value}s`,
  })),
  sizeOptions: [option('720p'), option('1080p')],
  aspectRatioOptions: VIDEO_LANDSCAPE_PORTRAIT_OPTIONS,
  defaultDuration: '4',
  defaultSize: '720p',
  defaultAspectRatio: '9:16',
}

const SEEDANCE_VIDEO_PRESET: VideoModelPreset = {
  durationOptions: ['5', '6', '10'].map((value) => ({
    value,
    label: `${value}s`,
  })),
  sizeOptions: [option('720p'), option('1080p')],
  aspectRatioOptions: VIDEO_RATIO_OPTIONS,
  defaultDuration: '10',
  defaultSize: '720p',
  defaultAspectRatio: '9:16',
}

export function getImageModelPreset(model: string): ImageModelPreset {
  const normalized = model.toLowerCase()
  if (normalized === 'gpt-image-2') return GPT_IMAGE_2_PRESET
  if (normalized === 'dall-e' || normalized === 'dall-e-2') {
    return DALL_E_2_PRESET
  }
  if (normalized === 'dall-e-3') return DALL_E_3_PRESET
  if (
    normalized === 'gpt-image-1' ||
    normalized === 'gpt-image-1-mini' ||
    normalized === 'chatgpt-image-latest'
  ) {
    return OPENAI_IMAGE_PRESET
  }
  if (normalized.includes('gemini') && normalized.includes('image')) {
    return GEMINI_IMAGE_PRESET
  }
  if (normalized.includes('banana')) return BANANA_IMAGE_PRESET
  if (
    normalized.includes('minimax') ||
    normalized === 'image-01' ||
    normalized.includes('seedream') ||
    normalized.includes('flux') ||
    normalized.includes('stable-diffusion') ||
    normalized.includes('midjourney')
  ) {
    return ASPECT_RATIO_IMAGE_PRESET
  }
  return DEFAULT_IMAGE_PRESET
}

export function getVideoModelPreset(model: string): VideoModelPreset {
  const normalized = model.toLowerCase()
  if (
    normalized === 'grok-imagine-video-1.5-preview' ||
    normalized === 'grok-imagine-video-1.5'
  ) {
    return GROK_15_VIDEO_PRESET
  }
  if (normalized === 'grok-imagine-1.0-video') return GROK_10_VIDEO_PRESET
  if (['veo31', 'veo31-ref', 'veo31-fast'].includes(normalized)) {
    return VEO31_VIDEO_PRESET
  }
  if (normalized.includes('seedance')) return SEEDANCE_VIDEO_PRESET
  return DEFAULT_VIDEO_PRESET
}

export function isImageModel(model: string) {
  return /(?:image|imagen|banana|dall-e|flux|stable-diffusion|midjourney|seedream|minimax|jimeng.*img|t2i|i2i)/i.test(
    model
  )
}

export function isVideoModel(model: string) {
  return /(?:video|sora|veo|kling|pika|wan-|wanx|hunyuanvideo|seedance|hailuo|vidu|jimeng|t2v|i2v|s2v)/i.test(
    model
  )
}
