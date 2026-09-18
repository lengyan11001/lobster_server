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
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Coins,
  GitBranch,
  HandCoins,
  Network,
  ReceiptText,
  RefreshCcw,
  Users,
  XCircle,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { formatQuota, formatTimestampToDate } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { StatusBadge, type StatusVariant } from '@/components/status-badge'

const PAGE_SIZE = 10

type ApiEnvelope<T> = {
  success: boolean
  message?: string
  data?: T
}

type PageEnvelope<T> = {
  page: number
  page_size: number
  total: number
  items: T[]
}

type AffiliateAdminAnomaly = {
  level: 'normal' | 'info' | 'warning' | 'critical' | string
  message: string
}

type AffiliateAdminSummary = {
  invited_users: number
  active_inviters: number
  pending_rewards: number
  history_rewards: number
  commission_total: number
  commission_level_1: number
  commission_level_2: number
  commission_last_7_days: number
  settlement_total: number
  settlement_last_7_days: number
  commission_records: number
  settlement_records: number
  high_pending_users: number
  zero_rate_warning: boolean
  first_rate: number
  second_rate: number
  anomalies: AffiliateAdminAnomaly[]
}

type AffiliateAdminCommissionRecord = {
  id: number
  inviter_id: number
  invitee_id: number
  topup_id: number
  trade_no: string
  level: number
  event_type: string
  gross_amount: number
  money: number
  rate: number
  commission_amount: number
  status: string
  created_at: number
  settled_at?: number
  inviter_username?: string
  inviter_display_name?: string
  invitee_username?: string
  invitee_display_name?: string
}

type AffiliateAdminSettlementRecord = {
  id: number
  user_id: number
  type: string
  status: string
  amount: number
  balance_before: number
  balance_after: number
  aff_quota_before: number
  aff_quota_after: number
  remark?: string
  created_at: number
  updated_at?: number
  username?: string
  display_name?: string
}

type AffiliateSmokeStep = {
  name: string
  passed: boolean
  expected?: string
  actual?: string
  message?: string
}

type AffiliateSmokeResult = {
  passed: boolean
  message: string
  first_rate: number
  second_rate: number
  gross_quota: number
  first_commission: number
  second_commission: number
  transfer_amount: number
  trade_no: string
  steps: AffiliateSmokeStep[]
}

type CommissionFilters = {
  inviterId: string
  inviteeId: string
  level: string
  tradeNo: string
  startTime: string
  endTime: string
}

type SettlementFilters = {
  userId: string
  status: string
  startTime: string
  endTime: string
}

type AdminTransferForm = {
  userId: string
  quota: string
  remark: string
}

const emptyCommissionFilters: CommissionFilters = {
  inviterId: '',
  inviteeId: '',
  level: '',
  tradeNo: '',
  startTime: '',
  endTime: '',
}

const emptySettlementFilters: SettlementFilters = {
  userId: '',
  status: '',
  startTime: '',
  endTime: '',
}

const emptyAdminTransferForm: AdminTransferForm = {
  userId: '',
  quota: '',
  remark: '',
}

function toTimestamp(value: string) {
  if (!value) return undefined
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return undefined
  return Math.floor(time / 1000)
}

function appendIfPresent(params: URLSearchParams, key: string, value?: string) {
  if (value && value.trim()) {
    params.set(key, value.trim())
  }
}

function appendDateIfPresent(
  params: URLSearchParams,
  key: string,
  value?: string
) {
  const timestamp = toTimestamp(value || '')
  if (timestamp) {
    params.set(key, String(timestamp))
  }
}

async function getAffiliateAdminSummary() {
  const res = await api.get<ApiEnvelope<AffiliateAdminSummary>>(
    '/api/affiliate/admin/summary'
  )
  return res.data.data ?? null
}

async function getAffiliateAdminCommissions(
  page: number,
  filters: CommissionFilters
) {
  const params = new URLSearchParams({
    p: String(page),
    page_size: String(PAGE_SIZE),
  })
  appendIfPresent(params, 'inviter_id', filters.inviterId)
  appendIfPresent(params, 'invitee_id', filters.inviteeId)
  appendIfPresent(params, 'level', filters.level)
  appendIfPresent(params, 'trade_no', filters.tradeNo)
  appendDateIfPresent(params, 'start_time', filters.startTime)
  appendDateIfPresent(params, 'end_time', filters.endTime)
  const res = await api.get<
    ApiEnvelope<PageEnvelope<AffiliateAdminCommissionRecord>>
  >(`/api/affiliate/admin/commissions?${params.toString()}`)
  return res.data.data ?? { page, page_size: PAGE_SIZE, total: 0, items: [] }
}

async function getAffiliateAdminSettlements(
  page: number,
  filters: SettlementFilters
) {
  const params = new URLSearchParams({
    p: String(page),
    page_size: String(PAGE_SIZE),
  })
  appendIfPresent(params, 'user_id', filters.userId)
  appendIfPresent(params, 'status', filters.status)
  appendDateIfPresent(params, 'start_time', filters.startTime)
  appendDateIfPresent(params, 'end_time', filters.endTime)
  const res = await api.get<
    ApiEnvelope<PageEnvelope<AffiliateAdminSettlementRecord>>
  >(`/api/affiliate/admin/settlements?${params.toString()}`)
  return res.data.data ?? { page, page_size: PAGE_SIZE, total: 0, items: [] }
}

async function transferAffiliateRewardsByAdmin(form: AdminTransferForm) {
  const userId = Number.parseInt(form.userId, 10)
  const quota = form.quota.trim() ? Number.parseInt(form.quota.trim(), 10) : 0

  const res = await api.post<ApiEnvelope<AffiliateAdminSettlementRecord>>(
    '/api/affiliate/admin/settlements/transfer',
    {
      user_id: Number.isNaN(userId) ? 0 : userId,
      quota: Number.isNaN(quota) ? 0 : quota,
      remark: form.remark.trim(),
    }
  )

  if (!res.data.success) {
    throw new Error(res.data.message || 'Failed to transfer rewards')
  }

  return res.data.data
}

async function runAffiliateSmokeTest() {
  const res = await api.post<ApiEnvelope<AffiliateSmokeResult>>(
    '/api/affiliate/admin/smoke-test'
  )
  return res.data.data ?? null
}

function formatMoney(money: number) {
  return `$${Number(money || 0).toFixed(2)}`
}

function formatRate(rate: number) {
  return `${Number(rate || 0)
    .toFixed(2)
    .replace(/\.?0+$/, '')}%`
}

function totalPages(total?: number) {
  return Math.max(1, Math.ceil((total || 0) / PAGE_SIZE))
}

function anomalyVariant(level?: string): StatusVariant {
  switch ((level || '').toLowerCase()) {
    case 'critical':
      return 'danger'
    case 'warning':
      return 'warning'
    case 'normal':
      return 'success'
    default:
      return 'neutral'
  }
}

function passVariant(passed?: boolean): StatusVariant {
  if (passed === undefined) return 'neutral'
  return passed ? 'success' : 'danger'
}

function MetricCard({
  title,
  value,
  hint,
  icon: Icon,
  loading,
}: {
  title: string
  value: string
  hint?: string
  icon: typeof Coins
  loading?: boolean
}) {
  return (
    <div className='bg-background/60 rounded-lg border p-3'>
      <div className='flex items-center justify-between gap-3'>
        <div className='text-muted-foreground text-xs'>{title}</div>
        <Icon className='text-muted-foreground size-4' />
      </div>
      {loading ? (
        <Skeleton className='mt-2 h-7 w-24' />
      ) : (
        <div className='mt-2 text-xl font-semibold'>{value}</div>
      )}
      {hint && <div className='text-muted-foreground mt-1 text-xs'>{hint}</div>}
    </div>
  )
}

function UserLabel({
  id,
  username,
  displayName,
}: {
  id: number
  username?: string
  displayName?: string
}) {
  return (
    <div className='min-w-[120px]'>
      <div className='font-medium'>{displayName || username || `#${id}`}</div>
      <div className='text-muted-foreground text-xs'>
        #{id}
        {username ? ` · ${username}` : ''}
      </div>
    </div>
  )
}

function PaginationBar({
  page,
  total,
  onPageChange,
}: {
  page: number
  total: number
  onPageChange: (page: number) => void
}) {
  const pages = totalPages(total)
  const { t } = useTranslation()
  return (
    <div className='flex items-center justify-between gap-3'>
      <div className='text-muted-foreground text-xs'>
        {t('Page {{page}} / {{totalPages}}', { page, totalPages: pages })}
      </div>
      <div className='flex items-center gap-2'>
        <Button
          variant='outline'
          size='sm'
          className='size-8 p-0'
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft className='size-4' />
        </Button>
        <Button
          variant='outline'
          size='sm'
          className='size-8 p-0'
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight className='size-4' />
        </Button>
      </div>
    </div>
  )
}

export function AffiliateAdminSection() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState('overview')
  const [commissionPage, setCommissionPage] = useState(1)
  const [settlementPage, setSettlementPage] = useState(1)
  const [commissionFilters, setCommissionFilters] = useState<CommissionFilters>(
    emptyCommissionFilters
  )
  const [appliedCommissionFilters, setAppliedCommissionFilters] =
    useState<CommissionFilters>(emptyCommissionFilters)
  const [settlementFilters, setSettlementFilters] = useState<SettlementFilters>(
    emptySettlementFilters
  )
  const [appliedSettlementFilters, setAppliedSettlementFilters] =
    useState<SettlementFilters>(emptySettlementFilters)
  const [affiliateSmokeResult, setAffiliateSmokeResult] =
    useState<AffiliateSmokeResult | null>(null)
  const [transferDialogOpen, setTransferDialogOpen] = useState(false)
  const [transferForm, setTransferForm] = useState<AdminTransferForm>(
    emptyAdminTransferForm
  )

  const summaryQuery = useQuery({
    queryKey: ['affiliate-admin-summary'],
    queryFn: getAffiliateAdminSummary,
  })
  const commissionQuery = useQuery({
    queryKey: [
      'affiliate-admin-commissions',
      commissionPage,
      appliedCommissionFilters,
    ],
    queryFn: () =>
      getAffiliateAdminCommissions(commissionPage, appliedCommissionFilters),
  })
  const settlementQuery = useQuery({
    queryKey: [
      'affiliate-admin-settlements',
      settlementPage,
      appliedSettlementFilters,
    ],
    queryFn: () =>
      getAffiliateAdminSettlements(settlementPage, appliedSettlementFilters),
  })
  const affiliateSmokeMutation = useMutation({
    mutationFn: runAffiliateSmokeTest,
    onSuccess: (data) => {
      if (!data) {
        toast.error(t('Affiliate self-test returned no result'))
        return
      }

      setAffiliateSmokeResult(data)
      refreshAll()
      if (data.passed) {
        toast.success(t('Affiliate self-test passed'))
      } else {
        toast.error(data.message || t('Affiliate self-test failed'))
      }
    },
    onError: () => {
      toast.error(t('Affiliate self-test failed'))
    },
  })
  const adminTransferMutation = useMutation({
    mutationFn: transferAffiliateRewardsByAdmin,
    onSuccess: () => {
      toast.success(t('Affiliate rewards transferred'))
      setTransferDialogOpen(false)
      setTransferForm(emptyAdminTransferForm)
      setSettlementPage(1)
      refreshAll()
    },
    onError: (error) => {
      toast.error(
        error instanceof Error
          ? error.message
          : t('Failed to transfer affiliate rewards')
      )
    },
  })

  const summary = summaryQuery.data
  const commissionData = commissionQuery.data
  const settlementData = settlementQuery.data
  const isFetching = useMemo(
    () =>
      summaryQuery.isFetching ||
      commissionQuery.isFetching ||
      settlementQuery.isFetching,
    [
      commissionQuery.isFetching,
      settlementQuery.isFetching,
      summaryQuery.isFetching,
    ]
  )

  const refreshAll = () => {
    summaryQuery.refetch()
    commissionQuery.refetch()
    settlementQuery.refetch()
  }

  const applyCommissionFilters = () => {
    setCommissionPage(1)
    setAppliedCommissionFilters({ ...commissionFilters })
  }

  const resetCommissionFilters = () => {
    setCommissionFilters(emptyCommissionFilters)
    setAppliedCommissionFilters(emptyCommissionFilters)
    setCommissionPage(1)
  }

  const applySettlementFilters = () => {
    setSettlementPage(1)
    setAppliedSettlementFilters({ ...settlementFilters })
  }

  const resetSettlementFilters = () => {
    setSettlementFilters(emptySettlementFilters)
    setAppliedSettlementFilters(emptySettlementFilters)
    setSettlementPage(1)
  }

  const submitAdminTransfer = () => {
    const userId = Number.parseInt(transferForm.userId, 10)
    const quota = transferForm.quota.trim()
      ? Number.parseInt(transferForm.quota.trim(), 10)
      : 0

    if (!userId || userId <= 0) {
      toast.error(t('Please enter a valid user ID'))
      return
    }
    if (transferForm.quota.trim() && (!quota || quota < 0)) {
      toast.error(t('Please enter a valid transfer quota'))
      return
    }

    adminTransferMutation.mutate(transferForm)
  }

  return (
    <div className='space-y-4'>
      <Card>
        <CardHeader>
          <CardTitle className='flex items-center gap-2'>
            <Network className='size-4' />
            {t('Affiliate Management')}
          </CardTitle>
          <CardDescription>
            {t('View site-wide referrals, commissions, and settlements.')}
          </CardDescription>
          <CardAction>
            <Button
              variant='outline'
              size='sm'
              onClick={refreshAll}
              disabled={isFetching}
            >
              <RefreshCcw className='size-4' />
              {t('Refresh')}
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList className='h-auto flex-wrap justify-start'>
              <TabsTrigger value='overview'>{t('Overview')}</TabsTrigger>
              <TabsTrigger value='commissions'>
                {t('Commission records')}
              </TabsTrigger>
              <TabsTrigger value='settlements'>
                {t('Settlement records')}
              </TabsTrigger>
            </TabsList>

            <TabsContent value='overview' className='mt-4 space-y-4'>
              <Card className='py-0'>
                <CardHeader className='border-b px-4 py-3'>
                  <CardTitle className='flex items-center gap-2 text-base'>
                    <GitBranch className='size-4' />
                    {t('Affiliate self-test')}
                  </CardTitle>
                  <CardDescription>
                    {t(
                      'Simulate A invites B, B invites C, C recharges, then verify level 1 commission, level 2 commission, idempotency, reward balance, and transfer to balance. Test data is rolled back automatically.'
                    )}
                  </CardDescription>
                  <CardAction>
                    <Button
                      size='sm'
                      onClick={() => affiliateSmokeMutation.mutate()}
                      disabled={affiliateSmokeMutation.isPending}
                    >
                      <GitBranch className='size-4' />
                      {affiliateSmokeMutation.isPending
                        ? t('Running...')
                        : t('Run self-test')}
                    </Button>
                  </CardAction>
                </CardHeader>
                <CardContent className='space-y-4 p-4'>
                  <div className='grid gap-3 md:grid-cols-2 xl:grid-cols-5'>
                    <MetricCard
                      title={t('Result')}
                      value={
                        affiliateSmokeResult
                          ? affiliateSmokeResult.passed
                            ? t('Passed')
                            : t('Failed')
                          : t('Not tested')
                      }
                      hint={affiliateSmokeResult?.message}
                      icon={GitBranch}
                    />
                    <MetricCard
                      title={t('Level 1 rate')}
                      value={
                        affiliateSmokeResult
                          ? formatRate(affiliateSmokeResult.first_rate)
                          : '-'
                      }
                      hint={t('Direct inviter')}
                      icon={ReceiptText}
                    />
                    <MetricCard
                      title={t('Level 2 rate')}
                      value={
                        affiliateSmokeResult
                          ? formatRate(affiliateSmokeResult.second_rate)
                          : '-'
                      }
                      hint={t('Upper-level inviter')}
                      icon={ReceiptText}
                    />
                    <MetricCard
                      title={t('Test recharge quota')}
                      value={
                        affiliateSmokeResult
                          ? formatQuota(affiliateSmokeResult.gross_quota)
                          : '-'
                      }
                      hint={t('Rolled back automatically')}
                      icon={Coins}
                    />
                    <MetricCard
                      title={t('Expected commission')}
                      value={
                        affiliateSmokeResult
                          ? `${formatQuota(
                              affiliateSmokeResult.first_commission
                            )} / ${formatQuota(
                              affiliateSmokeResult.second_commission
                            )}`
                          : '-'
                      }
                      hint={t('Level 1 / Level 2')}
                      icon={HandCoins}
                    />
                  </div>

                  {affiliateSmokeResult && (
                    <div className='space-y-2'>
                      <div className='text-muted-foreground flex flex-wrap items-center gap-3 text-xs'>
                        <span>
                          {t('Test order')}: {affiliateSmokeResult.trade_no}
                        </span>
                        <StatusBadge
                          variant={passVariant(affiliateSmokeResult.passed)}
                          label={
                            affiliateSmokeResult.passed
                              ? t('Passed')
                              : t('Failed')
                          }
                          copyable={false}
                        />
                      </div>
                      {affiliateSmokeResult.steps.map((step) => {
                        const Icon = step.passed ? CheckCircle2 : XCircle
                        return (
                          <div
                            key={step.name}
                            className={cn(
                              'flex items-start justify-between gap-3 rounded-lg border p-3',
                              step.passed
                                ? 'border-success/30 bg-success/5'
                                : 'border-destructive/30 bg-destructive/5'
                            )}
                          >
                            <div className='min-w-0 space-y-1'>
                              <div className='flex items-center gap-2'>
                                <Icon
                                  className={cn(
                                    'size-4 shrink-0',
                                    step.passed
                                      ? 'text-success'
                                      : 'text-destructive'
                                  )}
                                />
                                <span className='font-medium'>{step.name}</span>
                              </div>
                              {(step.expected || step.actual) && (
                                <div className='text-muted-foreground text-xs break-words'>
                                  {step.expected && (
                                    <span>
                                      {t('Expected')}: {step.expected}
                                    </span>
                                  )}
                                  {step.expected && step.actual && (
                                    <span className='mx-2'>/</span>
                                  )}
                                  {step.actual && (
                                    <span>
                                      {t('Actual')}: {step.actual}
                                    </span>
                                  )}
                                </div>
                              )}
                              {step.message && (
                                <div className='text-muted-foreground text-xs break-words'>
                                  {step.message}
                                </div>
                              )}
                            </div>
                            <StatusBadge
                              variant={passVariant(step.passed)}
                              label={step.passed ? t('Passed') : t('Failed')}
                              copyable={false}
                            />
                          </div>
                        )
                      })}
                    </div>
                  )}
                </CardContent>
              </Card>

              <div className='grid gap-3 md:grid-cols-2 xl:grid-cols-4'>
                <MetricCard
                  title={t('Invited users')}
                  value={String(summary?.invited_users ?? 0)}
                  hint={t('Users with inviter source')}
                  icon={Users}
                  loading={summaryQuery.isLoading}
                />
                <MetricCard
                  title={t('Active promoters')}
                  value={String(summary?.active_inviters ?? 0)}
                  hint={t('Users who created invite relationships')}
                  icon={Network}
                  loading={summaryQuery.isLoading}
                />
                <MetricCard
                  title={t('Pending rewards')}
                  value={formatQuota(summary?.pending_rewards ?? 0)}
                  hint={t('Current site-wide aff_quota')}
                  icon={Coins}
                  loading={summaryQuery.isLoading}
                />
                <MetricCard
                  title={t('Total rewards')}
                  value={formatQuota(summary?.history_rewards ?? 0)}
                  hint={t('Current site-wide aff_history')}
                  icon={Coins}
                  loading={summaryQuery.isLoading}
                />
                <MetricCard
                  title={t('Total commission')}
                  value={formatQuota(summary?.commission_total ?? 0)}
                  hint={`${t('Level 1')} ${formatQuota(
                    summary?.commission_level_1 ?? 0
                  )} / ${t('Level 2')} ${formatQuota(
                    summary?.commission_level_2 ?? 0
                  )}`}
                  icon={ReceiptText}
                  loading={summaryQuery.isLoading}
                />
                <MetricCard
                  title={t('Transferred balance')}
                  value={formatQuota(summary?.settlement_total ?? 0)}
                  hint={t('Completed settlements')}
                  icon={HandCoins}
                  loading={summaryQuery.isLoading}
                />
                <MetricCard
                  title={t('Last 7 days commission')}
                  value={formatQuota(summary?.commission_last_7_days ?? 0)}
                  hint={`${summary?.commission_records ?? 0} ${t('records')}`}
                  icon={ReceiptText}
                  loading={summaryQuery.isLoading}
                />
                <MetricCard
                  title={t('Last 7 days settlements')}
                  value={formatQuota(summary?.settlement_last_7_days ?? 0)}
                  hint={`${summary?.settlement_records ?? 0} ${t('records')}`}
                  icon={HandCoins}
                  loading={summaryQuery.isLoading}
                />
              </div>

              <Card className='py-0'>
                <CardHeader className='border-b px-4 py-3'>
                  <CardTitle className='flex items-center gap-2 text-base'>
                    <AlertTriangle className='size-4' />
                    {t('Anomaly tips')}
                  </CardTitle>
                  <CardDescription>
                    {t('Level 1 rate')} {formatRate(summary?.first_rate ?? 0)} /{' '}
                    {t('Level 2 rate')} {formatRate(summary?.second_rate ?? 0)}
                  </CardDescription>
                </CardHeader>
                <CardContent className='space-y-2 p-4'>
                  {(summary?.anomalies ?? []).map((item, index) => (
                    <div
                      key={`${item.level}-${index}`}
                      className='flex items-start justify-between gap-3 rounded-lg border p-3'
                    >
                      <div className='text-sm'>{t(item.message)}</div>
                      <StatusBadge
                        variant={anomalyVariant(item.level)}
                        label={item.level}
                        copyable={false}
                      />
                    </div>
                  ))}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value='commissions' className='mt-4 space-y-4'>
              <div className='grid gap-2 md:grid-cols-3 xl:grid-cols-6'>
                <Input
                  placeholder={t('Inviter ID')}
                  value={commissionFilters.inviterId}
                  onChange={(event) =>
                    setCommissionFilters((filters) => ({
                      ...filters,
                      inviterId: event.target.value,
                    }))
                  }
                />
                <Input
                  placeholder={t('Invitee ID')}
                  value={commissionFilters.inviteeId}
                  onChange={(event) =>
                    setCommissionFilters((filters) => ({
                      ...filters,
                      inviteeId: event.target.value,
                    }))
                  }
                />
                <NativeSelect
                  className='w-full'
                  value={commissionFilters.level}
                  onChange={(event) =>
                    setCommissionFilters((filters) => ({
                      ...filters,
                      level: event.target.value,
                    }))
                  }
                >
                  <NativeSelectOption value=''>
                    {t('All levels')}
                  </NativeSelectOption>
                  <NativeSelectOption value='1'>
                    {t('Level 1')}
                  </NativeSelectOption>
                  <NativeSelectOption value='2'>
                    {t('Level 2')}
                  </NativeSelectOption>
                </NativeSelect>
                <Input
                  placeholder={t('Top-up order number')}
                  value={commissionFilters.tradeNo}
                  onChange={(event) =>
                    setCommissionFilters((filters) => ({
                      ...filters,
                      tradeNo: event.target.value,
                    }))
                  }
                />
                <Input
                  type='datetime-local'
                  value={commissionFilters.startTime}
                  onChange={(event) =>
                    setCommissionFilters((filters) => ({
                      ...filters,
                      startTime: event.target.value,
                    }))
                  }
                />
                <Input
                  type='datetime-local'
                  value={commissionFilters.endTime}
                  onChange={(event) =>
                    setCommissionFilters((filters) => ({
                      ...filters,
                      endTime: event.target.value,
                    }))
                  }
                />
              </div>
              <div className='flex justify-end gap-2'>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={resetCommissionFilters}
                >
                  {t('Reset')}
                </Button>
                <Button size='sm' onClick={applyCommissionFilters}>
                  {t('Filter')}
                </Button>
              </div>

              <div className='overflow-hidden rounded-lg border'>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('Inviter')}</TableHead>
                      <TableHead>{t('Invitee')}</TableHead>
                      <TableHead>{t('Level')}</TableHead>
                      <TableHead>{t('Top-up order')}</TableHead>
                      <TableHead>{t('Top-up quota')}</TableHead>
                      <TableHead>{t('Payment amount')}</TableHead>
                      <TableHead>{t('Commission rate')}</TableHead>
                      <TableHead>{t('Commission quota')}</TableHead>
                      <TableHead>{t('Time')}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {commissionQuery.isLoading ? (
                      Array.from({ length: 5 }).map((_, index) => (
                        <TableRow key={index}>
                          <TableCell colSpan={9}>
                            <Skeleton className='h-8 w-full' />
                          </TableCell>
                        </TableRow>
                      ))
                    ) : (commissionData?.items ?? []).length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={9}
                          className='text-muted-foreground h-24 text-center'
                        >
                          {t('No commission records')}
                        </TableCell>
                      </TableRow>
                    ) : (
                      (commissionData?.items ?? []).map((record) => (
                        <TableRow key={record.id}>
                          <TableCell>
                            <UserLabel
                              id={record.inviter_id}
                              username={record.inviter_username}
                              displayName={record.inviter_display_name}
                            />
                          </TableCell>
                          <TableCell>
                            <UserLabel
                              id={record.invitee_id}
                              username={record.invitee_username}
                              displayName={record.invitee_display_name}
                            />
                          </TableCell>
                          <TableCell>
                            <Badge variant='outline'>
                              {record.level === 2 ? t('Level 2') : t('Level 1')}
                            </Badge>
                          </TableCell>
                          <TableCell className='max-w-[180px] truncate font-mono text-xs'>
                            {record.trade_no}
                          </TableCell>
                          <TableCell>
                            {formatQuota(record.gross_amount)}
                          </TableCell>
                          <TableCell>{formatMoney(record.money)}</TableCell>
                          <TableCell>{formatRate(record.rate)}</TableCell>
                          <TableCell>
                            {formatQuota(record.commission_amount)}
                          </TableCell>
                          <TableCell>
                            {record.created_at
                              ? formatTimestampToDate(record.created_at)
                              : '-'}
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
              <PaginationBar
                page={commissionPage}
                total={commissionData?.total ?? 0}
                onPageChange={setCommissionPage}
              />
            </TabsContent>

            <TabsContent value='settlements' className='mt-4 space-y-4'>
              <div className='border-border/70 bg-muted/20 flex flex-col gap-3 rounded-lg border p-4 md:flex-row md:items-center md:justify-between'>
                <div className='space-y-1'>
                  <div className='flex items-center gap-2 text-sm font-medium'>
                    <HandCoins className='size-4' />
                    {t('Admin settlement')}
                  </div>
                  <p className='text-muted-foreground text-sm'>
                    {t(
                      'Transfer a user pending affiliate rewards into their main balance and record an auditable settlement.'
                    )}
                  </p>
                </div>
                <Button size='sm' onClick={() => setTransferDialogOpen(true)}>
                  <HandCoins className='size-4' />
                  {t('Transfer rewards')}
                </Button>
              </div>
              <div className='grid gap-2 md:grid-cols-2 xl:grid-cols-4'>
                <Input
                  placeholder={t('User ID')}
                  value={settlementFilters.userId}
                  onChange={(event) =>
                    setSettlementFilters((filters) => ({
                      ...filters,
                      userId: event.target.value,
                    }))
                  }
                />
                <NativeSelect
                  className='w-full'
                  value={settlementFilters.status}
                  onChange={(event) =>
                    setSettlementFilters((filters) => ({
                      ...filters,
                      status: event.target.value,
                    }))
                  }
                >
                  <NativeSelectOption value=''>
                    {t('All statuses')}
                  </NativeSelectOption>
                  <NativeSelectOption value='completed'>
                    {t('Completed')}
                  </NativeSelectOption>
                </NativeSelect>
                <Input
                  type='datetime-local'
                  value={settlementFilters.startTime}
                  onChange={(event) =>
                    setSettlementFilters((filters) => ({
                      ...filters,
                      startTime: event.target.value,
                    }))
                  }
                />
                <Input
                  type='datetime-local'
                  value={settlementFilters.endTime}
                  onChange={(event) =>
                    setSettlementFilters((filters) => ({
                      ...filters,
                      endTime: event.target.value,
                    }))
                  }
                />
              </div>
              <div className='flex justify-end gap-2'>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={resetSettlementFilters}
                >
                  {t('Reset')}
                </Button>
                <Button size='sm' onClick={applySettlementFilters}>
                  {t('Filter')}
                </Button>
              </div>

              <div className='overflow-hidden rounded-lg border'>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('User')}</TableHead>
                      <TableHead>{t('Type')}</TableHead>
                      <TableHead>{t('Status')}</TableHead>
                      <TableHead>{t('Amount')}</TableHead>
                      <TableHead>{t('Balance change')}</TableHead>
                      <TableHead>{t('Pending reward change')}</TableHead>
                      <TableHead>{t('Time')}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {settlementQuery.isLoading ? (
                      Array.from({ length: 5 }).map((_, index) => (
                        <TableRow key={index}>
                          <TableCell colSpan={7}>
                            <Skeleton className='h-8 w-full' />
                          </TableCell>
                        </TableRow>
                      ))
                    ) : (settlementData?.items ?? []).length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={7}
                          className='text-muted-foreground h-24 text-center'
                        >
                          {t('No settlement records')}
                        </TableCell>
                      </TableRow>
                    ) : (
                      (settlementData?.items ?? []).map((record) => (
                        <TableRow key={record.id}>
                          <TableCell>
                            <UserLabel
                              id={record.user_id}
                              username={record.username}
                              displayName={record.display_name}
                            />
                          </TableCell>
                          <TableCell>{t('Transfer to balance')}</TableCell>
                          <TableCell>
                            <Badge variant='secondary'>{t('Completed')}</Badge>
                          </TableCell>
                          <TableCell>{formatQuota(record.amount)}</TableCell>
                          <TableCell>
                            {formatQuota(record.balance_before)} {'->'}{' '}
                            {formatQuota(record.balance_after)}
                          </TableCell>
                          <TableCell>
                            {formatQuota(record.aff_quota_before)} {'->'}{' '}
                            {formatQuota(record.aff_quota_after)}
                          </TableCell>
                          <TableCell>
                            {record.created_at
                              ? formatTimestampToDate(record.created_at)
                              : '-'}
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
              <PaginationBar
                page={settlementPage}
                total={settlementData?.total ?? 0}
                onPageChange={setSettlementPage}
              />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
      <Dialog open={transferDialogOpen} onOpenChange={setTransferDialogOpen}>
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>{t('Transfer affiliate rewards')}</DialogTitle>
            <DialogDescription>
              {t(
                'Enter 0 or leave quota empty to transfer all pending affiliate rewards for this user.'
              )}
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-4'>
            <div className='space-y-2'>
              <Label htmlFor='affiliate-admin-transfer-user'>
                {t('User ID')}
              </Label>
              <Input
                id='affiliate-admin-transfer-user'
                inputMode='numeric'
                value={transferForm.userId}
                onChange={(event) =>
                  setTransferForm((form) => ({
                    ...form,
                    userId: event.target.value,
                  }))
                }
                placeholder='123'
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='affiliate-admin-transfer-quota'>
                {t('Transfer quota')}
              </Label>
              <Input
                id='affiliate-admin-transfer-quota'
                inputMode='numeric'
                value={transferForm.quota}
                onChange={(event) =>
                  setTransferForm((form) => ({
                    ...form,
                    quota: event.target.value,
                  }))
                }
                placeholder={t('Leave empty to transfer all')}
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='affiliate-admin-transfer-remark'>
                {t('Remark')}
              </Label>
              <Textarea
                id='affiliate-admin-transfer-remark'
                value={transferForm.remark}
                onChange={(event) =>
                  setTransferForm((form) => ({
                    ...form,
                    remark: event.target.value,
                  }))
                }
                placeholder={t('Optional operation note')}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => setTransferDialogOpen(false)}
              disabled={adminTransferMutation.isPending}
            >
              {t('Cancel')}
            </Button>
            <Button
              onClick={submitAdminTransfer}
              disabled={adminTransferMutation.isPending}
            >
              <HandCoins className='size-4' />
              {adminTransferMutation.isPending
                ? t('Transferring...')
                : t('Confirm transfer')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
