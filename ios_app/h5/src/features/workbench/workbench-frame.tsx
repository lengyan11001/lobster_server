import { useRouterState } from '@tanstack/react-router'
import { PublicLayout } from '@/components/layout'

const WORKBENCH_PUBLIC_BASE = '/workbench'
const WORKBENCH_APP_BASE = '/workbench-app'

function getWorkbenchAppSrc(pathname: string, search: string, hash: string) {
  const appPath = pathname.startsWith(WORKBENCH_PUBLIC_BASE)
    ? pathname.replace(WORKBENCH_PUBLIC_BASE, WORKBENCH_APP_BASE)
    : `${WORKBENCH_APP_BASE}/chat`

  return `${appPath || WORKBENCH_APP_BASE}${search}${hash}`
}

export function WorkbenchFrame() {
  const location = useRouterState({ select: (state) => state.location })
  const src = getWorkbenchAppSrc(
    location.pathname,
    location.searchStr,
    location.hash
  )

  return (
    <PublicLayout showMainContainer={false}>
      <iframe
        className='block h-[calc(100svh-3rem)] w-full border-0 bg-background'
        src={src}
        title='AI 工作台'
      />
    </PublicLayout>
  )
}
