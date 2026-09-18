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
import { useState, useCallback } from 'react'

export interface ScreenCaptureOptions {
  onError?: (error: string) => void
}

export function useScreenCapture(options: ScreenCaptureOptions = {}) {
  const { onError } = options
  const [isCapturing, setIsCapturing] = useState(false)

  const captureScreen = useCallback(async (): Promise<string | null> => {
    setIsCapturing(true)

    try {
      // Check if API is supported
      if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
        throw new Error('Screen capture is not supported in this browser')
      }

      // Request screen capture
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
      })

      // Create video element
      const video = document.createElement('video')
      video.srcObject = stream
      video.autoplay = true

      // Wait for video to be ready
      await new Promise<void>((resolve) => {
        video.onloadedmetadata = () => {
          video.play()
          resolve()
        }
      })

      // Wait a bit for the first frame
      await new Promise((resolve) => setTimeout(resolve, 100))

      // Create canvas and capture frame
      const canvas = document.createElement('canvas')
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight

      const ctx = canvas.getContext('2d')
      if (!ctx) {
        throw new Error('Failed to get canvas context')
      }

      ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

      // Stop all tracks
      stream.getTracks().forEach((track) => track.stop())

      // Convert to data URL
      const dataUrl = canvas.toDataURL('image/png')

      setIsCapturing(false)
      return dataUrl
    } catch (err) {
      const errorMessage =
        err instanceof Error ? err.message : 'Screen capture failed'
      onError?.(errorMessage)
      setIsCapturing(false)
      return null
    }
  }, [onError])

  return {
    captureScreen,
    isCapturing,
  }
}
