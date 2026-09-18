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
import { useMemo, useState } from 'react'
import {
  CheckIcon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlusIcon,
  SearchIcon,
  Trash2Icon,
  XIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { PlaygroundConversation } from '../types'

interface PlaygroundHistorySidebarProps {
  conversations: PlaygroundConversation[]
  activeId: number | null
  isLoading?: boolean
  onNewConversation: () => void
  onSelectConversation: (id: number) => void
  onRenameConversation: (id: number, title: string) => void
  onDeleteConversation: (id: number) => void
  className?: string
}

export function PlaygroundHistorySidebar({
  conversations,
  activeId,
  isLoading = false,
  onNewConversation,
  onSelectConversation,
  onRenameConversation,
  onDeleteConversation,
  className,
}: PlaygroundHistorySidebarProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingTitle, setEditingTitle] = useState('')

  const filteredConversations = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return conversations
    return conversations.filter((conversation) =>
      conversation.title.toLowerCase().includes(keyword)
    )
  }, [conversations, query])

  const startRename = (conversation: PlaygroundConversation) => {
    setEditingId(conversation.id)
    setEditingTitle(conversation.title)
  }

  const finishRename = () => {
    if (!editingId) return
    const title = editingTitle.trim()
    if (title) {
      onRenameConversation(editingId, title)
    }
    setEditingId(null)
    setEditingTitle('')
  }

  const cancelRename = () => {
    setEditingId(null)
    setEditingTitle('')
  }

  return (
    <aside
      className={cn(
        'bg-background flex h-full min-h-0 shrink-0 flex-col rounded-lg border',
        className
      )}
    >
      <div className='space-y-2 border-b p-2.5'>
        <Button
          className='w-full justify-start'
          onClick={onNewConversation}
          size='sm'
          variant='outline'
        >
          <PlusIcon className='size-4' />
          {t('New conversation')}
        </Button>
        <div className='relative'>
          <SearchIcon className='text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2' />
          <Input
            className='h-8 rounded-lg pl-8 text-sm'
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('Search conversations')}
          />
        </div>
      </div>

      <ScrollArea className='min-h-0 flex-1'>
        <div className='space-y-1 p-2'>
          {isLoading ? (
            <div className='text-muted-foreground px-2 py-6 text-center text-sm'>
              {t('Loading conversations')}
            </div>
          ) : filteredConversations.length === 0 ? (
            <div className='text-muted-foreground px-2 py-6 text-center text-sm'>
              {t('No conversations yet')}
            </div>
          ) : (
            filteredConversations.map((conversation) => {
              const isActive = conversation.id === activeId
              const isEditing = conversation.id === editingId

              return (
                <div
                  className={cn(
                    'group flex h-8 items-center gap-2 rounded-md px-2 text-sm',
                    isActive ? 'bg-muted text-foreground' : 'hover:bg-muted/70'
                  )}
                  key={conversation.id}
                >
                  <MessageSquareIcon className='text-muted-foreground size-4 shrink-0' />
                  {isEditing ? (
                    <div className='flex min-w-0 flex-1 items-center gap-1'>
                      <Input
                        autoFocus
                        className='h-7 rounded-md px-2 text-sm'
                        value={editingTitle}
                        onChange={(event) =>
                          setEditingTitle(event.target.value)
                        }
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') finishRename()
                          if (event.key === 'Escape') cancelRename()
                        }}
                      />
                      <Button
                        size='icon-sm'
                        variant='ghost'
                        onClick={finishRename}
                        aria-label={t('Save')}
                      >
                        <CheckIcon className='size-4' />
                      </Button>
                      <Button
                        size='icon-sm'
                        variant='ghost'
                        onClick={cancelRename}
                        aria-label={t('Cancel')}
                      >
                        <XIcon className='size-4' />
                      </Button>
                    </div>
                  ) : (
                    <>
                      <button
                        className='min-w-0 flex-1 truncate text-left'
                        onClick={() => onSelectConversation(conversation.id)}
                        type='button'
                      >
                        {conversation.title}
                      </button>
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          render={
                            <Button
                              className='opacity-0 group-hover:opacity-100 data-[popup-open]:opacity-100'
                              size='icon-sm'
                              variant='ghost'
                              aria-label={t('More actions')}
                            />
                          }
                        >
                          <MoreHorizontalIcon className='size-4' />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align='end' className='w-36'>
                          <DropdownMenuItem
                            onSelect={() => startRename(conversation)}
                          >
                            <PencilIcon className='size-4' />
                            {t('Rename')}
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            variant='destructive'
                            onSelect={() =>
                              onDeleteConversation(conversation.id)
                            }
                          >
                            <Trash2Icon className='size-4' />
                            {t('Delete')}
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </>
                  )}
                </div>
              )
            })
          )}
        </div>
      </ScrollArea>
    </aside>
  )
}
