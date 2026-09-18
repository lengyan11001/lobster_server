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
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ElementType,
} from 'react'
import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Coins,
  Gift,
  HandCoins,
  RefreshCw,
  ReceiptText,
  Share2,
  Sparkles,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { getSelf } from '@/lib/api'
import { formatQuota, formatTimestampToDate } from '@/lib/format'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CopyButton } from '@/components/copy-button'
import { SectionPageLayout } from '@/components/layout'
import {
  getAffiliateCommissions,
  getAffiliateSettlements,
  getInvitedUsers,
  isApiSuccess,
} from '@/features/wallet/api'
import { TransferDialog } from '@/features/wallet/components/dialogs/transfer-dialog'
import { useAffiliate } from '@/features/wallet/hooks'
import type {
  AffiliateCommissionRecord,
  AffiliateSettlementRecord,
  InvitedUserRecord,
  UserWalletData,
} from '@/features/wallet/types'

const PAGE_SIZE = 10

type StatCardProps = {
  title: string
  value: string
  description: string
  icon: ElementType
  loading?: boolean
}

function StatCard(props: StatCardProps) {
  const Icon = props.icon

  return (
    <Card className='py-0'>
      <CardContent className='flex items-center gap-3 p-4'>
        <div className='bg-muted flex size-10 shrink-0 items-center justify-center rounded-lg'>
          <Icon className='text-muted-foreground size-5' />
        </div>
        <div className='min-w-0 flex-1'>
          <p className='text-muted-foreground truncate text-xs font-medium'>
            {props.title}
          </p>
          {props.loading ? (
            <Skeleton className='mt-1 h-6 w-24' />
          ) : (
            <p className='truncate text-xl font-semibold tabular-nums'>
              {props.value}
            </p>
          )}
          <p className='text-muted-foreground mt-0.5 truncate text-xs'>
            {props.description}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

function EmptyInvites() {
  const { t } = useTranslation()

  return (
    <div className='text-muted-foreground flex min-h-52 flex-col items-center justify-center rounded-lg border border-dashed text-center'>
      <Users className='mb-3 size-8 opacity-50' />
      <p className='text-sm font-medium'>{t('No invited users yet')}</p>
      <p className='mt-1 max-w-sm text-xs'>
        {t('Users who register through your promotion link will appear here.')}
      </p>
    </div>
  )
}

function EmptyCommissions() {
  const { t } = useTranslation()

  return (
    <div className='text-muted-foreground flex min-h-52 flex-col items-center justify-center rounded-lg border border-dashed text-center'>
      <ReceiptText className='mb-3 size-8 opacity-50' />
      <p className='text-sm font-medium'>
        {t('No recharge commission records yet')}
      </p>
      <p className='mt-1 max-w-sm text-xs'>
        {t(
          'Records are generated automatically after an invited user recharges successfully and the commission rate is greater than 0.'
        )}
      </p>
    </div>
  )
}

function EmptySettlements() {
  const { t } = useTranslation()

  return (
    <div className='text-muted-foreground flex min-h-40 flex-col items-center justify-center rounded-lg border border-dashed text-center'>
      <HandCoins className='mb-3 size-8 opacity-50' />
      <p className='text-sm font-medium'>{t('No settlement records yet')}</p>
      <p className='mt-1 max-w-sm text-xs'>
        {t(
          'Reward transfer records will appear here after you move pending rewards to your balance.'
        )}
      </p>
    </div>
  )
}

function formatRate(rate: number) {
  return `${Number(rate || 0)
    .toFixed(2)
    .replace(/\.?0+$/, '')}%`
}

function formatMoney(money: number) {
  return `$${Number(money || 0).toFixed(2)}`
}

function commissionStatusLabel(status: string, t: (key: string) => string) {
  switch (status) {
    case 'open':
      return t('Added to pending rewards')
    case 'transferred':
      return t('Transferred to balance')
    case 'reversed':
      return t('Reversed')
    default:
      return status || '-'
  }
}

function settlementStatusLabel(status: string, t: (key: string) => string) {
  switch (status) {
    case 'completed':
      return t('Completed')
    case 'pending':
      return t('Pending')
    case 'rejected':
      return t('Rejected')
    default:
      return status || '-'
  }
}

function settlementTypeLabel(type: string, t: (key: string) => string) {
  switch (type) {
    case 'quota_transfer':
      return t('Transfer to balance')
    default:
      return type || '-'
  }
}

export function PromotionCenter() {
  const { t } = useTranslation()
  const [user, setUser] = useState<UserWalletData | null>(null)
  const [userLoading, setUserLoading] = useState(true)
  const [records, setRecords] = useState<InvitedUserRecord[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [recordsLoading, setRecordsLoading] = useState(true)
  const [commissionRecords, setCommissionRecords] = useState<
    AffiliateCommissionRecord[]
  >([])
  const [commissionTotal, setCommissionTotal] = useState(0)
  const [commissionPage, setCommissionPage] = useState(1)
  const [commissionLoading, setCommissionLoading] = useState(true)
  const [settlementRecords, setSettlementRecords] = useState<
    AffiliateSettlementRecord[]
  >([])
  const [settlementTotal, setSettlementTotal] = useState(0)
  const [settlementPage, setSettlementPage] = useState(1)
  const [settlementLoading, setSettlementLoading] = useState(true)
  const [transferDialogOpen, setTransferDialogOpen] = useState(false)

  const {
    affiliateCode,
    affiliateLink,
    loading: affiliateLoading,
    transferQuota,
    transferring,
  } = useAffiliate()

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const commissionTotalPages = Math.max(
    1,
    Math.ceil(commissionTotal / PAGE_SIZE)
  )
  const settlementTotalPages = Math.max(
    1,
    Math.ceil(settlementTotal / PAGE_SIZE)
  )

  const fetchUser = useCallback(async () => {
    try {
      const response = await getSelf()
      if (response.success && response.data) {
        setUser(response.data as UserWalletData)
      }
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('Failed to fetch promotion user data:', error)
      toast.error(t('Failed to load promotion account data'))
    } finally {
      setUserLoading(false)
    }
  }, [t])

  const fetchRecords = useCallback(async () => {
    try {
      const response = await getInvitedUsers(page, PAGE_SIZE)
      if (isApiSuccess(response) && response.data) {
        setRecords(response.data.items || [])
        setTotal(response.data.total || 0)
        return
      }

      setRecords([])
      setTotal(0)
      toast.error(response.message || t('Failed to load invited users'))
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('Failed to fetch invited users:', error)
      setRecords([])
      setTotal(0)
      toast.error(t('Failed to load invited users'))
    } finally {
      setRecordsLoading(false)
    }
  }, [page, t])

  const fetchCommissions = useCallback(async () => {
    try {
      const response = await getAffiliateCommissions(commissionPage, PAGE_SIZE)
      if (isApiSuccess(response) && response.data) {
        setCommissionRecords(response.data.items || [])
        setCommissionTotal(response.data.total || 0)
        return
      }

      setCommissionRecords([])
      setCommissionTotal(0)
      toast.error(
        response.message || t('Failed to load recharge commission records')
      )
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('Failed to fetch affiliate commissions:', error)
      setCommissionRecords([])
      setCommissionTotal(0)
      toast.error(t('Failed to load recharge commission records'))
    } finally {
      setCommissionLoading(false)
    }
  }, [commissionPage, t])

  const fetchSettlements = useCallback(async () => {
    try {
      const response = await getAffiliateSettlements(settlementPage, PAGE_SIZE)
      if (isApiSuccess(response) && response.data) {
        setSettlementRecords(response.data.items || [])
        setSettlementTotal(response.data.total || 0)
        return
      }

      setSettlementRecords([])
      setSettlementTotal(0)
      toast.error(response.message || t('Failed to load settlement records'))
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error('Failed to fetch affiliate settlements:', error)
      setSettlementRecords([])
      setSettlementTotal(0)
      toast.error(t('Failed to load settlement records'))
    } finally {
      setSettlementLoading(false)
    }
  }, [settlementPage, t])

  const refreshAll = useCallback(async () => {
    setUserLoading(true)
    setRecordsLoading(true)
    setCommissionLoading(true)
    setSettlementLoading(true)
    await Promise.all([
      fetchUser(),
      fetchRecords(),
      fetchCommissions(),
      fetchSettlements(),
    ])
  }, [fetchCommissions, fetchRecords, fetchSettlements, fetchUser])

  useEffect(() => {
    let cancelled = false

    void getSelf()
      .then((response) => {
        if (!cancelled && response.success && response.data) {
          setUser(response.data as UserWalletData)
        }
      })
      .catch((error) => {
        // eslint-disable-next-line no-console
        console.error('Failed to fetch promotion user data:', error)
        if (!cancelled) {
          toast.error(t('Failed to load promotion account data'))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setUserLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [t])

  useEffect(() => {
    let cancelled = false

    void getInvitedUsers(page, PAGE_SIZE)
      .then((response) => {
        if (cancelled) return

        if (isApiSuccess(response) && response.data) {
          setRecords(response.data.items || [])
          setTotal(response.data.total || 0)
          return
        }

        setRecords([])
        setTotal(0)
        toast.error(response.message || t('Failed to load invited users'))
      })
      .catch((error) => {
        // eslint-disable-next-line no-console
        console.error('Failed to fetch invited users:', error)
        if (!cancelled) {
          setRecords([])
          setTotal(0)
          toast.error(t('Failed to load invited users'))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRecordsLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [page, t])

  useEffect(() => {
    let cancelled = false

    void getAffiliateCommissions(commissionPage, PAGE_SIZE)
      .then((response) => {
        if (cancelled) return

        if (isApiSuccess(response) && response.data) {
          setCommissionRecords(response.data.items || [])
          setCommissionTotal(response.data.total || 0)
          return
        }

        setCommissionRecords([])
        setCommissionTotal(0)
        toast.error(
          response.message || t('Failed to load recharge commission records')
        )
      })
      .catch((error) => {
        // eslint-disable-next-line no-console
        console.error('Failed to fetch affiliate commissions:', error)
        if (!cancelled) {
          setCommissionRecords([])
          setCommissionTotal(0)
          toast.error(t('Failed to load recharge commission records'))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setCommissionLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [commissionPage, t])

  useEffect(() => {
    let cancelled = false

    void getAffiliateSettlements(settlementPage, PAGE_SIZE)
      .then((response) => {
        if (cancelled) return

        if (isApiSuccess(response) && response.data) {
          setSettlementRecords(response.data.items || [])
          setSettlementTotal(response.data.total || 0)
          return
        }

        setSettlementRecords([])
        setSettlementTotal(0)
        toast.error(response.message || t('Failed to load settlement records'))
      })
      .catch((error) => {
        // eslint-disable-next-line no-console
        console.error('Failed to fetch affiliate settlements:', error)
        if (!cancelled) {
          setSettlementRecords([])
          setSettlementTotal(0)
          toast.error(t('Failed to load settlement records'))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setSettlementLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [settlementPage, t])

  const progressItems = useMemo(
    () => [
      {
        title: t('Generate promotion link'),
        description: affiliateCode
          ? t('Generated, ready to copy and share')
          : t('Generating'),
        done: Boolean(affiliateCode),
      },
      {
        title: t('Invite users to register'),
        description:
          (user?.aff_count ?? 0) > 0
            ? t('Invited {{count}} users', { count: user?.aff_count ?? 0 })
            : t('Waiting for users to register through your link'),
        done: (user?.aff_count ?? 0) > 0,
      },
      {
        title: t('Claim registration rewards'),
        description:
          (user?.aff_quota ?? 0) > 0
            ? t('Rewards are waiting to be transferred to balance')
            : t('Rewards can be transferred to balance once available'),
        done: (user?.aff_history_quota ?? 0) > 0,
      },
    ],
    [
      affiliateCode,
      t,
      user?.aff_count,
      user?.aff_history_quota,
      user?.aff_quota,
    ]
  )

  const handleTransfer = async (amount: number) => {
    const success = await transferQuota(amount)
    if (success) {
      setSettlementLoading(true)
      await fetchUser()
      await fetchSettlements()
    }
    return success
  }

  return (
    <>
      <SectionPageLayout>
        <SectionPageLayout.Title>
          {t('Promotion Center')}
        </SectionPageLayout.Title>
        <SectionPageLayout.Actions>
          <Button
            variant='outline'
            size='sm'
            onClick={refreshAll}
            disabled={
              userLoading ||
              recordsLoading ||
              commissionLoading ||
              settlementLoading
            }
          >
            <RefreshCw className='size-4' />
            {t('Refresh')}
          </Button>
        </SectionPageLayout.Actions>
        <SectionPageLayout.Content>
          <div className='mx-auto flex w-full max-w-7xl flex-col gap-4 sm:gap-5'>
            <div className='grid gap-3 md:grid-cols-3'>
              <StatCard
                title={t('Pending rewards')}
                value={formatQuota(user?.aff_quota ?? 0)}
                description={t('Available to transfer to balance')}
                icon={Coins}
                loading={userLoading}
              />
              <StatCard
                title={t('Total promotion rewards')}
                value={formatQuota(user?.aff_history_quota ?? 0)}
                description={t(
                  'Registration and recharge commissions combined'
                )}
                icon={Gift}
                loading={userLoading}
              />
              <StatCard
                title={t('Invited users')}
                value={String(user?.aff_count ?? 0)}
                description={t('Number of users registered through your link')}
                icon={Users}
                loading={userLoading}
              />
            </div>

            <div className='grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]'>
              <Card className='py-0'>
                <CardHeader className='border-b px-4 py-3'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <CardTitle className='flex items-center gap-2 text-base'>
                      <Share2 className='size-4' />
                      {t('My promotion link')}
                    </CardTitle>
                    <Badge variant='secondary'>
                      {t('Registration reward')}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className='space-y-4 p-4'>
                  <div className='grid gap-3 sm:grid-cols-[180px_minmax(0,1fr)]'>
                    <div>
                      <p className='text-muted-foreground text-xs font-medium'>
                        {t('Invitation code')}
                      </p>
                      {affiliateLoading ? (
                        <Skeleton className='mt-2 h-10 w-full' />
                      ) : (
                        <div className='mt-2 flex items-center gap-2'>
                          <Input
                            value={affiliateCode}
                            readOnly
                            className='h-10 font-mono text-sm'
                          />
                          <CopyButton
                            value={affiliateCode}
                            variant='outline'
                            className='size-10 shrink-0'
                            tooltip={t('Copy invitation code')}
                            aria-label={t('Copy invitation code')}
                          />
                        </div>
                      )}
                    </div>

                    <div>
                      <p className='text-muted-foreground text-xs font-medium'>
                        {t('Promotion registration link')}
                      </p>
                      {affiliateLoading ? (
                        <Skeleton className='mt-2 h-10 w-full' />
                      ) : (
                        <div className='mt-2 flex items-center gap-2'>
                          <Input
                            value={affiliateLink}
                            readOnly
                            className='h-10 min-w-0 font-mono text-xs'
                          />
                          <CopyButton
                            value={affiliateLink}
                            variant='outline'
                            className='size-10 shrink-0'
                            tooltip={t('Copy promotion link')}
                            aria-label={t('Copy promotion link')}
                          />
                        </div>
                      )}
                    </div>
                  </div>

                  <div className='bg-muted/25 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-3'>
                    <div className='min-w-0'>
                      <p className='text-sm font-medium'>
                        {t('Transfer rewards to balance')}
                      </p>
                      <p className='text-muted-foreground mt-0.5 text-xs'>
                        {t(
                          'Registration rewards and recharge commissions first enter pending rewards, then move to your main balance after transfer.'
                        )}
                      </p>
                    </div>
                    <Button
                      onClick={() => setTransferDialogOpen(true)}
                      disabled={(user?.aff_quota ?? 0) <= 0}
                      className='shrink-0'
                    >
                      <HandCoins className='size-4' />
                      {t('Transfer to balance')}
                    </Button>
                  </div>
                </CardContent>
              </Card>

              <Card className='py-0'>
                <CardHeader className='border-b px-4 py-3'>
                  <CardTitle className='flex items-center gap-2 text-base'>
                    <Sparkles className='size-4' />
                    {t('Promotion progress')}
                  </CardTitle>
                </CardHeader>
                <CardContent className='space-y-3 p-4'>
                  {progressItems.map((item, index) => (
                    <div key={item.title} className='flex gap-3'>
                      <div className='flex flex-col items-center'>
                        <div
                          className={
                            item.done
                              ? 'flex size-7 items-center justify-center rounded-full bg-emerald-500 text-white'
                              : 'bg-muted text-muted-foreground flex size-7 items-center justify-center rounded-full'
                          }
                        >
                          {item.done ? (
                            <CheckCircle2 className='size-4' />
                          ) : (
                            <span className='text-xs font-semibold'>
                              {index + 1}
                            </span>
                          )}
                        </div>
                        {index < progressItems.length - 1 && (
                          <div className='bg-border mt-2 h-8 w-px' />
                        )}
                      </div>
                      <div className='min-w-0 pb-2'>
                        <p className='text-sm font-medium'>{item.title}</p>
                        <p className='text-muted-foreground mt-0.5 text-xs'>
                          {item.description}
                        </p>
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>

            <div className='grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]'>
              <Card className='py-0'>
                <CardHeader className='border-b px-4 py-3'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <CardTitle className='flex items-center gap-2 text-base'>
                      <Users className='size-4' />
                      {t('Invited users')}
                    </CardTitle>
                    <Badge variant='outline'>
                      {t('{{count}} users total', { count: total })}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className='p-4'>
                  {recordsLoading ? (
                    <div className='space-y-2'>
                      {Array.from({ length: 5 }).map((_, index) => (
                        <Skeleton key={index} className='h-11 w-full' />
                      ))}
                    </div>
                  ) : records.length === 0 ? (
                    <EmptyInvites />
                  ) : (
                    <div className='space-y-3'>
                      <div className='overflow-hidden rounded-lg border'>
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>{t('User ID')}</TableHead>
                              <TableHead>{t('Username')}</TableHead>
                              <TableHead>{t('Display Name')}</TableHead>
                              <TableHead>{t('Registration Time')}</TableHead>
                              <TableHead>{t('Reward Status')}</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {records.map((record) => (
                              <TableRow key={record.id}>
                                <TableCell className='font-mono'>
                                  {record.id}
                                </TableCell>
                                <TableCell>{record.username}</TableCell>
                                <TableCell>
                                  {record.display_name || '-'}
                                </TableCell>
                                <TableCell>
                                  {record.created_at
                                    ? formatTimestampToDate(record.created_at)
                                    : '-'}
                                </TableCell>
                                <TableCell>
                                  <Badge variant='secondary'>
                                    {t('Registration bound')}
                                  </Badge>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>

                      <div className='flex flex-col items-center gap-3 sm:flex-row sm:justify-between'>
                        <div className='text-muted-foreground text-xs'>
                          {t('Page {{page}} / {{totalPages}}', {
                            page,
                            totalPages,
                          })}
                        </div>
                        <div className='flex items-center gap-2'>
                          <Button
                            variant='outline'
                            size='sm'
                            onClick={() => {
                              setRecordsLoading(true)
                              setPage((value) => value - 1)
                            }}
                            disabled={page <= 1}
                            className='size-8 p-0'
                          >
                            <ChevronLeft className='size-4' />
                          </Button>
                          <Button
                            variant='outline'
                            size='sm'
                            onClick={() => {
                              setRecordsLoading(true)
                              setPage((value) => value + 1)
                            }}
                            disabled={page >= totalPages}
                            className='size-8 p-0'
                          >
                            <ChevronRight className='size-4' />
                          </Button>
                        </div>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              <div className='space-y-4'>
                <Card className='py-0'>
                  <CardHeader className='border-b px-4 py-3'>
                    <div className='flex flex-wrap items-center justify-between gap-2'>
                      <CardTitle className='flex items-center gap-2 text-base'>
                        <ReceiptText className='size-4' />
                        {t('Recharge commission records')}
                      </CardTitle>
                      <Badge variant='outline'>
                        {t('{{count}} records total', {
                          count: commissionTotal,
                        })}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent className='p-4'>
                    {commissionLoading ? (
                      <div className='space-y-2'>
                        {Array.from({ length: 5 }).map((_, index) => (
                          <Skeleton key={index} className='h-16 w-full' />
                        ))}
                      </div>
                    ) : commissionRecords.length === 0 ? (
                      <EmptyCommissions />
                    ) : (
                      <div className='space-y-3'>
                        <div className='space-y-2'>
                          {commissionRecords.map((record) => (
                            <div
                              key={record.id}
                              className='rounded-lg border px-3 py-2.5'
                            >
                              <div className='flex items-start justify-between gap-3'>
                                <div className='min-w-0'>
                                  <p className='truncate text-sm font-medium'>
                                    {record.invitee_display_name ||
                                      record.invitee_username ||
                                      t('User #{{id}}', {
                                        id: record.invitee_id,
                                      })}
                                  </p>
                                  <p className='text-muted-foreground mt-0.5 truncate font-mono text-[11px]'>
                                    {record.trade_no}
                                  </p>
                                </div>
                                <div className='flex shrink-0 items-center gap-2'>
                                  <Badge variant='outline'>
                                    {record.level === 2
                                      ? t('Level 2')
                                      : t('Level 1')}
                                  </Badge>
                                  <Badge variant='secondary'>
                                    {commissionStatusLabel(record.status, t)}
                                  </Badge>
                                </div>
                              </div>

                              <div className='mt-3 grid grid-cols-2 gap-2 text-xs'>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Recharge quota')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatQuota(record.gross_amount)}
                                  </p>
                                </div>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Payment amount')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatMoney(record.money)}
                                  </p>
                                </div>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Commission rate')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatRate(record.rate)}
                                  </p>
                                </div>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Commission level')}
                                  </p>
                                  <p className='font-medium'>
                                    {record.level === 2
                                      ? t('Level 2')
                                      : t('Level 1')}
                                  </p>
                                </div>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Commission quota')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatQuota(record.commission_amount)}
                                  </p>
                                </div>
                              </div>

                              <p className='text-muted-foreground mt-3 text-xs'>
                                {record.created_at
                                  ? formatTimestampToDate(record.created_at)
                                  : '-'}
                              </p>
                            </div>
                          ))}
                        </div>

                        <div className='flex items-center justify-between'>
                          <div className='text-muted-foreground text-xs'>
                            {t('Page {{page}} / {{totalPages}}', {
                              page: commissionPage,
                              totalPages: commissionTotalPages,
                            })}
                          </div>
                          <div className='flex items-center gap-2'>
                            <Button
                              variant='outline'
                              size='sm'
                              onClick={() => {
                                setCommissionLoading(true)
                                setCommissionPage((value) => value - 1)
                              }}
                              disabled={commissionPage <= 1}
                              className='size-8 p-0'
                            >
                              <ChevronLeft className='size-4' />
                            </Button>
                            <Button
                              variant='outline'
                              size='sm'
                              onClick={() => {
                                setCommissionLoading(true)
                                setCommissionPage((value) => value + 1)
                              }}
                              disabled={commissionPage >= commissionTotalPages}
                              className='size-8 p-0'
                            >
                              <ChevronRight className='size-4' />
                            </Button>
                          </div>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>

                <Card className='py-0'>
                  <CardHeader className='border-b px-4 py-3'>
                    <div className='flex flex-wrap items-center justify-between gap-2'>
                      <CardTitle className='flex items-center gap-2 text-base'>
                        <HandCoins className='size-4' />
                        {t('Settlement records')}
                      </CardTitle>
                      <Badge variant='outline'>
                        {t('{{count}} records total', {
                          count: settlementTotal,
                        })}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent className='p-4'>
                    {settlementLoading ? (
                      <div className='space-y-2'>
                        {Array.from({ length: 3 }).map((_, index) => (
                          <Skeleton key={index} className='h-16 w-full' />
                        ))}
                      </div>
                    ) : settlementRecords.length === 0 ? (
                      <EmptySettlements />
                    ) : (
                      <div className='space-y-3'>
                        <div className='space-y-2'>
                          {settlementRecords.map((record) => (
                            <div
                              key={record.id}
                              className='rounded-lg border px-3 py-2.5'
                            >
                              <div className='flex items-start justify-between gap-3'>
                                <div className='min-w-0'>
                                  <p className='text-sm font-medium'>
                                    {settlementTypeLabel(record.type, t)}
                                  </p>
                                  <p className='text-muted-foreground mt-0.5 text-xs'>
                                    {record.created_at
                                      ? formatTimestampToDate(record.created_at)
                                      : '-'}
                                  </p>
                                </div>
                                <Badge variant='secondary'>
                                  {settlementStatusLabel(record.status, t)}
                                </Badge>
                              </div>

                              <div className='mt-3 grid grid-cols-2 gap-2 text-xs'>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Transferred amount')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatQuota(record.amount)}
                                  </p>
                                </div>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Balance after')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatQuota(record.balance_after)}
                                  </p>
                                </div>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Pending before')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatQuota(record.aff_quota_before)}
                                  </p>
                                </div>
                                <div>
                                  <p className='text-muted-foreground'>
                                    {t('Pending after')}
                                  </p>
                                  <p className='font-medium'>
                                    {formatQuota(record.aff_quota_after)}
                                  </p>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>

                        <div className='flex items-center justify-between'>
                          <div className='text-muted-foreground text-xs'>
                            {t('Page {{page}} / {{totalPages}}', {
                              page: settlementPage,
                              totalPages: settlementTotalPages,
                            })}
                          </div>
                          <div className='flex items-center gap-2'>
                            <Button
                              variant='outline'
                              size='sm'
                              onClick={() => {
                                setSettlementLoading(true)
                                setSettlementPage((value) => value - 1)
                              }}
                              disabled={settlementPage <= 1}
                              className='size-8 p-0'
                            >
                              <ChevronLeft className='size-4' />
                            </Button>
                            <Button
                              variant='outline'
                              size='sm'
                              onClick={() => {
                                setSettlementLoading(true)
                                setSettlementPage((value) => value + 1)
                              }}
                              disabled={settlementPage >= settlementTotalPages}
                              className='size-8 p-0'
                            >
                              <ChevronRight className='size-4' />
                            </Button>
                          </div>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>
          </div>
        </SectionPageLayout.Content>
      </SectionPageLayout>

      <TransferDialog
        open={transferDialogOpen}
        onOpenChange={setTransferDialogOpen}
        onConfirm={handleTransfer}
        availableQuota={user?.aff_quota ?? 0}
        transferring={transferring}
      />
    </>
  )
}
