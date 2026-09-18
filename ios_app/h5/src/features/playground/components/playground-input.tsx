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
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  BotIcon,
  FilmIcon,
  PaperclipIcon,
  FileIcon,
  ImageIcon,
  ScreenShareIcon,
  CameraIcon,
  GlobeIcon,
  SendIcon,
  SquareIcon,
  XIcon,
  Loader2Icon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  PromptInput,
  PromptInputButton,
  PromptInputFooter,
  PromptInputTextarea,
  PromptInputTools,
  type PromptInputMessage,
} from '@/components/ai-elements/prompt-input'
import { ModelGroupSelector } from '@/components/model-group-selector'
import { useFileUpload } from '../hooks/use-file-upload'
import { useScreenCapture } from '../hooks/use-screen-capture'
import {
  getDisplayImageUrl,
  getImageModelPreset,
  getVideoModelPreset,
} from '../lib'
import type { ModelOption, GroupOption, PlaygroundMode } from '../types'

interface ImageAttachmentRequest {
  id: string
  name: string
  url: string
  type?: string
  size?: number
}

const PREFERRED_VIDEO_ASPECT_RATIO = '9:16'
const PREFERRED_VIDEO_DURATION = '10'

interface PlaygroundInputProps {
  onSubmit: (
    text: string,
    files?: Array<{
      id: string
      name: string
      type: string
      url: string
      size: number
    }>
  ) => void
  onGenerateImage?: (
    text: string,
    options: { size: string; quality: string; n: number },
    files?: Array<{
      id: string
      name: string
      type: string
      url: string
      size: number
    }>
  ) => void
  onGenerateVideo?: (
    text: string,
    options: {
      aspectRatio: string
      duration: number
      size: string
      generateAudio: boolean
    },
    files?: Array<{
      id: string
      name: string
      type: string
      url: string
      size: number
    }>
  ) => void
  onStop?: () => void
  disabled?: boolean
  isGenerating?: boolean
  mode: PlaygroundMode
  onModeChange: (value: PlaygroundMode) => void
  models: ModelOption[]
  modelValue: string
  onModelChange: (value: string) => void
  isModelLoading?: boolean
  groups: GroupOption[]
  groupValue: string
  onGroupChange: (value: string) => void
  imageAttachmentRequest?: ImageAttachmentRequest | null
}

function getImageReferenceNumber(
  files: Array<{ type: string }>,
  currentIndex: number
) {
  return (
    files
      .slice(0, currentIndex + 1)
      .filter((file) => file.type.startsWith('image/')).length || 1
  )
}

function handleSelectValueChange(setValue: (value: string) => void) {
  return (value: string | null) => {
    if (value) setValue(value)
  }
}

function modelRequiresVideoImage(model: string) {
  return model.toLowerCase() === 'grok-imagine-video-1.5-preview'
}

export function PlaygroundInput({
  onSubmit,
  onGenerateImage,
  onGenerateVideo,
  onStop,
  disabled,
  isGenerating,
  mode,
  onModeChange,
  models,
  modelValue,
  onModelChange,
  isModelLoading = false,
  groups,
  groupValue,
  onGroupChange,
  imageAttachmentRequest,
}: PlaygroundInputProps) {
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const [imageSize, setImageSize] = useState('1024x1024')
  const [imageQuality, setImageQuality] = useState('auto')
  const [imageCount, setImageCount] = useState('1')
  const [videoAspectRatio, setVideoAspectRatio] = useState(
    PREFERRED_VIDEO_ASPECT_RATIO
  )
  const [videoDuration, setVideoDuration] = useState(PREFERRED_VIDEO_DURATION)
  const [videoSize, setVideoSize] = useState('480p')
  const [videoGenerateAudio, setVideoGenerateAudio] = useState(true)
  const imagePreset = useMemo(
    () => getImageModelPreset(modelValue),
    [modelValue]
  )
  const videoPreset = useMemo(
    () => getVideoModelPreset(modelValue),
    [modelValue]
  )
  const videoDurationOptions = videoPreset.durationOptions
  const videoSizeOptions = videoPreset.sizeOptions
  const videoAspectRatioOptions = videoPreset.aspectRatioOptions

  useEffect(() => {
    if (!imagePreset.sizeOptions.some((size) => size.value === imageSize)) {
      setImageSize(imagePreset.defaultSize)
    }
    if (
      !imagePreset.qualityOptions.some(
        (quality) => quality.value === imageQuality
      )
    ) {
      setImageQuality(imagePreset.defaultQuality)
    }
  }, [
    imagePreset.defaultQuality,
    imagePreset.defaultSize,
    imagePreset.qualityOptions,
    imagePreset.sizeOptions,
    imageQuality,
    imageSize,
  ])

  useEffect(() => {
    if (
      !videoDurationOptions.some((duration) => duration.value === videoDuration)
    ) {
      setVideoDuration(videoPreset.defaultDuration)
    }
    if (!videoSizeOptions.some((size) => size.value === videoSize)) {
      setVideoSize(videoPreset.defaultSize)
    }
    if (
      !videoAspectRatioOptions.some((ratio) => ratio.value === videoAspectRatio)
    ) {
      setVideoAspectRatio(videoPreset.defaultAspectRatio)
    }
  }, [
    videoAspectRatio,
    videoAspectRatioOptions,
    videoDuration,
    videoDurationOptions,
    videoPreset.defaultAspectRatio,
    videoPreset.defaultDuration,
    videoPreset.defaultSize,
    videoSize,
    videoSizeOptions,
  ])

  useEffect(() => {
    if (mode !== 'video') return

    const preferredDuration = videoDurationOptions.some(
      (duration) => duration.value === PREFERRED_VIDEO_DURATION
    )
      ? PREFERRED_VIDEO_DURATION
      : videoPreset.defaultDuration
    const preferredAspectRatio = videoAspectRatioOptions.some(
      (ratio) => ratio.value === PREFERRED_VIDEO_ASPECT_RATIO
    )
      ? PREFERRED_VIDEO_ASPECT_RATIO
      : videoPreset.defaultAspectRatio

    setVideoDuration(preferredDuration)
    setVideoAspectRatio(preferredAspectRatio)
    setVideoSize(videoPreset.defaultSize)
  }, [
    mode,
    videoAspectRatioOptions,
    videoDurationOptions,
    videoPreset.defaultAspectRatio,
    videoPreset.defaultDuration,
    videoPreset.defaultSize,
  ])

  // File upload hook
  const {
    files,
    addFiles,
    addUploadedFile,
    removeFile,
    clearFiles,
    openFileDialog,
    fileInputRef,
    handleFileInputChange,
  } = useFileUpload({
    onError: (error) => toast.error(error),
  })
  const handledImageAttachmentRequestId = useRef<string | null>(null)

  useEffect(() => {
    if (
      !imageAttachmentRequest ||
      handledImageAttachmentRequestId.current === imageAttachmentRequest.id
    ) {
      return
    }

    handledImageAttachmentRequestId.current = imageAttachmentRequest.id
    addUploadedFile({
      id: imageAttachmentRequest.id,
      name: imageAttachmentRequest.name,
      size: imageAttachmentRequest.size ?? 0,
      type: imageAttachmentRequest.type ?? 'image/png',
      url: imageAttachmentRequest.url,
    })
    toast.success(t('Image added to input'))
  }, [addUploadedFile, imageAttachmentRequest, t])

  // Screen capture hook
  const { captureScreen, isCapturing } = useScreenCapture({
    onError: (error) => toast.error(error),
  })

  const isModelSelectDisabled =
    disabled || isModelLoading || models.length === 0
  const isGroupSelectDisabled = disabled || groups.length === 0

  const handleSubmit = (message: PromptInputMessage) => {
    if (!message.text?.trim() || disabled) return

    // Check if any files are still uploading
    const hasUploadingFiles = files.some((f) => f.isUploading)
    if (hasUploadingFiles) {
      toast.error(t('Please wait for files to finish uploading'))
      return
    }

    // Check if any files have errors
    const hasErrorFiles = files.some((f) => f.error)
    if (hasErrorFiles) {
      toast.error(t('Please remove failed uploads before sending'))
      return
    }

    // Pass files to parent
    const uploadedFiles = files.filter((f) => !f.isUploading && !f.error)
    const submittedFiles = uploadedFiles.length > 0 ? uploadedFiles : undefined

    if (
      mode === 'video' &&
      modelRequiresVideoImage(modelValue) &&
      !uploadedFiles.some((file) => file.type.startsWith('image/'))
    ) {
      toast.error(t('This video model requires an uploaded image'))
      return
    }

    if (mode === 'image') {
      onGenerateImage?.(
        message.text,
        {
          size: imageSize,
          quality: imageQuality,
          n: Number(imageCount),
        },
        submittedFiles
      )
    } else if (mode === 'video') {
      onGenerateVideo?.(
        message.text,
        {
          aspectRatio: videoAspectRatio,
          duration: Number(videoDuration),
          size: videoSize,
          generateAudio: videoGenerateAudio,
        },
        submittedFiles
      )
    } else {
      onSubmit(message.text, submittedFiles)
    }

    setText('')
    clearFiles()
  }

  const handleFileAction = async (action: string) => {
    switch (action) {
      case 'upload-file':
        openFileDialog()
        break
      case 'upload-photo':
        openFileDialog('image/*')
        break
      case 'take-screenshot': {
        const dataUrl = await captureScreen()
        if (dataUrl) {
          // Convert data URL to File
          const response = await fetch(dataUrl)
          const blob = await response.blob()
          const file = new File([blob], `screenshot-${Date.now()}.png`, {
            type: 'image/png',
          })
          addFiles([file])
        }
        break
      }
      case 'take-photo':
        toast.info(t('Feature in development'), {
          description: action,
        })
        break
      default:
        break
    }
  }

  const submitLabel =
    mode === 'image'
      ? t('Generate image')
      : mode === 'video'
        ? t('Generate video')
        : t('Send')
  const placeholder =
    mode === 'image'
      ? t('Describe the image you want')
      : mode === 'video'
        ? t('Describe the video you want')
        : t('Ask anything')

  return (
    <div className='grid shrink-0 gap-1.5 px-1 md:pb-3'>
      <PromptInput
        groupClassName='rounded-md bg-background shadow-none'
        onSubmit={handleSubmit}
      >
        <div className='flex flex-wrap items-center justify-between gap-2 px-2 py-1.5'>
          <Tabs
            value={mode}
            onValueChange={(value) => onModeChange(value as PlaygroundMode)}
          >
            <TabsList className='h-7 rounded-md p-0.5'>
              <TabsTrigger value='chat'>
                <BotIcon className='size-4' />
                {t('Chat')}
              </TabsTrigger>
              <TabsTrigger value='image'>
                <ImageIcon className='size-4' />
                {t('Image')}
              </TabsTrigger>
              <TabsTrigger value='video'>
                <FilmIcon className='size-4' />
                {t('Video')}
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <ModelGroupSelector
            selectedModel={modelValue}
            models={models}
            onModelChange={onModelChange}
            selectedGroup={groupValue}
            groups={groups}
            onGroupChange={onGroupChange}
            disabled={isModelSelectDisabled || isGroupSelectDisabled}
          />
        </div>

        {mode !== 'chat' && (
          <div className='grid gap-2 border-t px-2 py-1.5 sm:flex sm:flex-wrap sm:items-center'>
            {mode === 'image' ? (
              <>
                <Select
                  value={imageSize}
                  onValueChange={handleSelectValueChange(setImageSize)}
                >
                  <SelectTrigger size='sm' className='w-full sm:w-36'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {imagePreset.sizeOptions.map((size) => (
                      <SelectItem key={size.value} value={size.value}>
                        {size.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={imageQuality}
                  onValueChange={handleSelectValueChange(setImageQuality)}
                >
                  <SelectTrigger size='sm' className='w-full sm:w-28'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {imagePreset.qualityOptions.map((quality) => (
                      <SelectItem key={quality.value} value={quality.value}>
                        {t(quality.label)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={imageCount}
                  onValueChange={handleSelectValueChange(setImageCount)}
                >
                  <SelectTrigger size='sm' className='w-full sm:w-24'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='1'>1</SelectItem>
                    <SelectItem value='2'>2</SelectItem>
                    <SelectItem value='4'>4</SelectItem>
                  </SelectContent>
                </Select>
              </>
            ) : (
              <>
                <Select
                  value={videoAspectRatio}
                  onValueChange={handleSelectValueChange(setVideoAspectRatio)}
                >
                  <SelectTrigger size='sm' className='w-full sm:w-28'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {videoAspectRatioOptions.map((ratio) => (
                      <SelectItem key={ratio.value} value={ratio.value}>
                        {ratio.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={videoDuration}
                  onValueChange={handleSelectValueChange(setVideoDuration)}
                >
                  <SelectTrigger size='sm' className='w-full sm:w-28'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {videoDurationOptions.map((duration) => (
                      <SelectItem key={duration.value} value={duration.value}>
                        {duration.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={videoSize}
                  onValueChange={handleSelectValueChange(setVideoSize)}
                >
                  <SelectTrigger size='sm' className='w-full sm:w-28'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {videoSizeOptions.map((size) => (
                      <SelectItem key={size.value} value={size.value}>
                        {size.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Label className='flex h-8 items-center gap-2 rounded-lg border px-3 text-xs font-medium'>
                  <Switch
                    size='sm'
                    checked={videoGenerateAudio}
                    onCheckedChange={setVideoGenerateAudio}
                  />
                  {t('Audio')}
                </Label>
              </>
            )}
          </div>
        )}

        {/* File attachments preview */}
        {files.length > 0 && (
          <div className='flex flex-wrap gap-2 border-t px-2 pt-2'>
            {files.map((file, index) => {
              const isImage = file.type.startsWith('image/')
              const displayName = isImage
                ? t('Reference image {{number}}', {
                    number: getImageReferenceNumber(files, index),
                  })
                : file.name

              return (
                <div
                  key={file.id}
                  className='group bg-background relative flex size-20 shrink-0 overflow-hidden rounded-lg border text-xs shadow-sm'
                >
                  {isImage && file.url ? (
                    <img
                      alt={displayName}
                      className='absolute inset-0 size-full object-cover'
                      src={getDisplayImageUrl(file.url)}
                    />
                  ) : (
                    <div className='bg-muted flex size-full items-center justify-center'>
                      <FileIcon className='text-muted-foreground size-6' />
                    </div>
                  )}

                  <div className='bg-background/90 absolute inset-x-0 bottom-0 px-1.5 py-1 backdrop-blur'>
                    <div className='truncate font-medium'>{displayName}</div>
                    {file.error && (
                      <div className='text-destructive truncate'>
                        {t('Error')}
                      </div>
                    )}
                  </div>

                  {file.isUploading && (
                    <div className='bg-background/70 absolute inset-0 flex items-center justify-center'>
                      <Loader2Icon className='size-5 animate-spin' />
                    </div>
                  )}

                  {!file.isUploading && (
                    <button
                      aria-label={t('Remove attachment')}
                      className='bg-background/90 text-muted-foreground hover:text-destructive absolute top-1 right-1 flex size-6 items-center justify-center rounded-md opacity-100 shadow-sm transition-colors sm:opacity-0 sm:group-hover:opacity-100'
                      onClick={() => removeFile(file.id)}
                      type='button'
                    >
                      <XIcon size={14} />
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}

        <PromptInputTextarea
          autoComplete='off'
          autoCorrect='off'
          autoCapitalize='off'
          spellCheck={false}
          className='min-h-10 px-2 py-1.5 text-sm'
          disabled={disabled}
          onChange={(event) => setText(event.target.value)}
          placeholder={placeholder}
          value={text}
        />

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type='file'
          multiple
          className='hidden'
          onChange={handleFileInputChange}
        />

        <PromptInputFooter className='border-t p-1.5'>
          <PromptInputTools>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <PromptInputButton
                    className='border font-medium'
                    disabled={disabled || isCapturing}
                    size='xs'
                    variant='outline'
                  />
                }
              >
                <PaperclipIcon size={16} />
                <span className='hidden sm:inline'>{t('Attach')}</span>
                <span className='sr-only sm:hidden'>{t('Attach')}</span>
              </DropdownMenuTrigger>
              <DropdownMenuContent align='start'>
                <DropdownMenuItem
                  onClick={() => handleFileAction('upload-file')}
                >
                  <FileIcon className='mr-2' size={16} />
                  {t('Upload file')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleFileAction('upload-photo')}
                >
                  <ImageIcon className='mr-2' size={16} />
                  {t('Upload photo')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleFileAction('take-screenshot')}
                >
                  <ScreenShareIcon className='mr-2' size={16} />
                  {t('Take screenshot')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleFileAction('take-photo')}
                >
                  <CameraIcon className='mr-2' size={16} />
                  {t('Take photo')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            <PromptInputButton
              className='border font-medium'
              disabled={disabled}
              onClick={() => toast.info(t('Search feature in development'))}
              size='xs'
              variant='outline'
            >
              <GlobeIcon size={16} />
              <span className='hidden sm:inline'>{t('Search')}</span>
              <span className='sr-only sm:hidden'>{t('Search')}</span>
            </PromptInputButton>
          </PromptInputTools>

          <div className='flex items-center gap-1.5 md:gap-2'>
            {isGenerating && onStop ? (
              <PromptInputButton
                className='text-foreground font-medium'
                onClick={onStop}
                size='xs'
                variant='secondary'
              >
                <SquareIcon className='fill-current' size={16} />
                <span className='hidden sm:inline'>{t('Stop')}</span>
                <span className='sr-only sm:hidden'>{t('Stop')}</span>
              </PromptInputButton>
            ) : (
              <PromptInputButton
                className='text-foreground font-medium'
                disabled={disabled || !text.trim()}
                size='xs'
                type='submit'
                variant='secondary'
              >
                <SendIcon size={16} />
                <span className='hidden sm:inline'>{submitLabel}</span>
                <span className='sr-only sm:hidden'>{submitLabel}</span>
              </PromptInputButton>
            )}
          </div>
        </PromptInputFooter>
      </PromptInput>
    </div>
  )
}
