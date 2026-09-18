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
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { formatTimestampToDate } from '@/lib/format'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useInvitedUsers } from '../../hooks'

interface InvitedUsersDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function InvitedUsersDialog({
  open,
  onOpenChange,
}: InvitedUsersDialogProps) {
  const { t } = useTranslation()
  const { records, total, page, pageSize, loading, setPage } =
    useInvitedUsers(open)
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='flex max-h-[calc(100dvh-2rem)] flex-col max-sm:h-dvh max-sm:w-screen max-sm:max-w-none max-sm:rounded-none sm:max-w-3xl'>
        <DialogHeader>
          <DialogTitle>{t('Invited Users')}</DialogTitle>
          <DialogDescription>
            {t('Users who registered through your referral link')}
          </DialogDescription>
        </DialogHeader>

        <div className='min-h-0 flex-1 space-y-3'>
          <ScrollArea className='h-[calc(100dvh-14rem)] pr-3 sm:h-[420px] sm:pr-4'>
            {loading ? (
              <div className='space-y-2'>
                {Array.from({ length: 5 }).map((_, index) => (
                  <Skeleton key={index} className='h-11 w-full' />
                ))}
              </div>
            ) : records.length === 0 ? (
              <div className='text-muted-foreground flex h-64 flex-col items-center justify-center text-center'>
                <p className='text-sm font-medium'>
                  {t('No invited users yet')}
                </p>
                <p className='mt-1 text-xs'>
                  {t('Users who register through your link will appear here')}
                </p>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t('User ID')}</TableHead>
                    <TableHead>{t('Username')}</TableHead>
                    <TableHead>{t('Display Name')}</TableHead>
                    <TableHead>{t('Created At')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {records.map((record) => (
                    <TableRow key={record.id}>
                      <TableCell className='font-mono'>{record.id}</TableCell>
                      <TableCell>{record.username}</TableCell>
                      <TableCell>{record.display_name || '-'}</TableCell>
                      <TableCell>
                        {record.created_at
                          ? formatTimestampToDate(record.created_at)
                          : '-'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </ScrollArea>

          {!loading && records.length > 0 && (
            <div className='flex flex-col items-center gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between'>
              <div className='text-muted-foreground text-xs sm:text-sm'>
                {t('Showing')} {(page - 1) * pageSize + 1}-
                {Math.min(page * pageSize, total)} {t('of')} {total}
              </div>
              <div className='flex items-center gap-2'>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => setPage(page - 1)}
                  disabled={page <= 1}
                  className='h-8 w-8 p-0'
                >
                  <ChevronLeft className='h-4 w-4' />
                </Button>
                <div className='text-muted-foreground flex items-center gap-1 text-sm'>
                  <span className='font-medium'>{page}</span>
                  <span>/</span>
                  <span>{totalPages}</span>
                </div>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => setPage(page + 1)}
                  disabled={page >= totalPages}
                  className='h-8 w-8 p-0'
                >
                  <ChevronRight className='h-4 w-4' />
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
