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
import { useState, useCallback, useRef } from 'react'
import { nanoid } from 'nanoid'
import { api } from '@/lib/api'

export interface UploadedFile {
  id: string
  name: string
  size: number
  type: string
  url: string
  isUploading?: boolean
  error?: string
}

interface UseFileUploadOptions {
  maxSize?: number
  acceptedTypes?: string[]
  onError?: (error: string) => void
}

interface FileUploadApiResponse {
  id: string
  bytes: number
  filename: string
  url: string
}

interface UploadErrorResponse {
  error?: {
    message?: string
  }
  message?: string
}

interface UploadHttpError {
  response?: {
    data?: UploadErrorResponse
    status?: number
    statusText?: string
  }
}

const DEFAULT_MAX_SIZE = 20 * 1024 * 1024 // 20MB
const DEFAULT_ACCEPTED_TYPES = [
  'image/png',
  'image/jpeg',
  'image/jpg',
  'image/gif',
  'image/webp',
  'application/pdf',
  'text/plain',
  'text/markdown',
  'application/json',
  'text/csv',
]

export function useFileUpload(options: UseFileUploadOptions = {}) {
  const {
    maxSize = DEFAULT_MAX_SIZE,
    acceptedTypes = DEFAULT_ACCEPTED_TYPES,
    onError,
  } = options

  const [files, setFiles] = useState<UploadedFile[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)

  const validateFile = useCallback(
    (file: File): string | null => {
      // Check file size
      if (file.size > maxSize) {
        return `File size exceeds ${Math.round(maxSize / (1024 * 1024))}MB limit`
      }

      // Check file type
      if (acceptedTypes.length > 0 && !acceptedTypes.includes(file.type)) {
        return `File type ${file.type} is not supported`
      }

      return null
    },
    [maxSize, acceptedTypes]
  )

  const uploadFile = useCallback(async (file: File): Promise<UploadedFile> => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('purpose', 'assistants')

    const response = await api
      .post<FileUploadApiResponse>('/pg/files', formData, {
        skipErrorHandler: true,
      } as Record<string, unknown>)
      .catch((err: unknown) => {
        const response = (err as UploadHttpError)?.response
        const message =
          response?.data?.error?.message ||
          response?.data?.message ||
          (response
            ? `Upload failed: ${response.status} ${response.statusText}`
            : err instanceof Error
              ? err.message
              : 'Upload failed')
        throw new Error(message)
      })

    const data = response.data

    return {
      id: data.id,
      name: data.filename,
      size: data.bytes,
      type: file.type,
      url: data.url,
    }
  }, [])

  const addFiles = useCallback(
    async (filesToAdd: File[] | FileList) => {
      const fileArray = Array.from(filesToAdd)

      for (const file of fileArray) {
        // Validate file
        const error = validateFile(file)
        if (error) {
          onError?.(error)
          continue
        }

        // Create temporary file entry
        const tempId = nanoid()
        const tempFile: UploadedFile = {
          id: tempId,
          name: file.name,
          size: file.size,
          type: file.type,
          url: '',
          isUploading: true,
        }

        setFiles((prev) => [...prev, tempFile])

        try {
          // Upload file
          const uploadedFile = await uploadFile(file)

          // Update with uploaded file info
          setFiles((prev) =>
            prev.map((f) =>
              f.id === tempId ? { ...uploadedFile, isUploading: false } : f
            )
          )
        } catch (err) {
          const errorMessage =
            err instanceof Error ? err.message : 'Upload failed'
          onError?.(errorMessage)

          // Update with error
          setFiles((prev) =>
            prev.map((f) =>
              f.id === tempId
                ? { ...f, isUploading: false, error: errorMessage }
                : f
            )
          )
        }
      }
    },
    [validateFile, uploadFile, onError]
  )

  const addUploadedFile = useCallback((file: UploadedFile) => {
    setFiles((prev) => {
      if (prev.some((item) => item.url === file.url)) return prev
      return [...prev, file]
    })
  }, [])

  const removeFile = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id))
  }, [])

  const clearFiles = useCallback(() => {
    setFiles([])
  }, [])

  const openFileDialog = useCallback(
    (accept?: string) => {
      if (fileInputRef.current) {
        if (accept) {
          fileInputRef.current.accept = accept
        } else {
          fileInputRef.current.accept = acceptedTypes.join(',')
        }
        fileInputRef.current.click()
      }
    },
    [acceptedTypes]
  )

  const handleFileInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const selectedFiles = event.target.files
      if (selectedFiles && selectedFiles.length > 0) {
        addFiles(selectedFiles)
      }
      // Reset input value to allow selecting the same file again
      event.target.value = ''
    },
    [addFiles]
  )

  return {
    files,
    addFiles,
    addUploadedFile,
    removeFile,
    clearFiles,
    openFileDialog,
    fileInputRef,
    handleFileInputChange,
  }
}
