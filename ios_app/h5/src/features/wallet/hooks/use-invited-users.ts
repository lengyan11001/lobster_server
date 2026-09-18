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
import { useCallback, useEffect, useState } from 'react'
import i18next from 'i18next'
import { toast } from 'sonner'
import { getInvitedUsers, isApiSuccess } from '../api'
import type { InvitedUserRecord } from '../types'

export function useInvitedUsers(open: boolean) {
  const [records, setRecords] = useState<InvitedUserRecord[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(10)
  const [loading, setLoading] = useState(false)

  const fetchInvitedUsers = useCallback(async () => {
    if (!open) return

    setLoading(true)
    try {
      const response = await getInvitedUsers(page, pageSize)
      if (isApiSuccess(response) && response.data) {
        setRecords(response.data.items || [])
        setTotal(response.data.total || 0)
      } else {
        toast.error(response.message || i18next.t('Failed to load invited users'))
        setRecords([])
        setTotal(0)
      }
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('Failed to fetch invited users:', error)
      toast.error(i18next.t('Failed to load invited users'))
      setRecords([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }, [open, page, pageSize])

  useEffect(() => {
    fetchInvitedUsers()
  }, [fetchInvitedUsers])

  useEffect(() => {
    if (open) {
      setPage(1)
    }
  }, [open])

  return {
    records,
    total,
    page,
    pageSize,
    loading,
    setPage,
    refresh: fetchInvitedUsers,
  }
}
