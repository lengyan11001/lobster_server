import { createFileRoute } from '@tanstack/react-router'
import { UsageGuide } from '@/features/usage-guide'

export const Route = createFileRoute('/help')({
  component: UsageGuide,
})

