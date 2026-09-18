import { createFileRoute } from '@tanstack/react-router'
import { WorkbenchFrame } from '@/features/workbench/workbench-frame'

export const Route = createFileRoute('/workbench/multiframe')({
  component: WorkbenchFrame,
})
