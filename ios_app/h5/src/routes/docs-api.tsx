import { createFileRoute } from '@tanstack/react-router'
import { useEffect } from 'react'
import { EXTERNAL_API_DOCS_URL } from '@/lib/constants'

function ApiDocsRedirect() {
  useEffect(() => {
    window.location.replace(EXTERNAL_API_DOCS_URL)
  }, [])

  return (
    <main className='flex min-h-screen items-center justify-center p-6'>
      <a
        href={EXTERNAL_API_DOCS_URL}
        className='text-primary text-sm font-medium hover:underline'
      >
        正在打开 API 文档...
      </a>
    </main>
  )
}

export const Route = createFileRoute('/docs-api')({
  component: ApiDocsRedirect,
})
