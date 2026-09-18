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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Bell,
  CheckCircle2,
  Eye,
  GitBranch,
  Image,
  MessageSquare,
  Play,
  RefreshCcw,
  Server,
  Users,
  Video,
  XCircle,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api } from '@/lib/api'
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { StatusBadge, type StatusVariant } from '@/components/status-badge'

type ApiEnvelope<T> = {
  success: boolean
  message?: string
  data?: T
  summary?: SmokeSummary
  targets?: SmokeTarget[]
  results?: SmokeResult[]
  dry_run?: boolean
}

type RuntimeData = {
  collected_at?: number
  cpu?: number
  load?: {
    load1?: number
    load5?: number
    load15?: number
  }
  memory?: {
    used_percent?: number
    used_gib?: number
    total_gib?: number
  }
  disk?: {
    path?: string
    used_percent?: number
    used_gib?: number
    total_gib?: number
  }
  warnings?: string[]
  errors?: Record<string, string>
}

type MonitorSummary = {
  runtime?: RuntimeData
  usage?: {
    totals?: {
      requests?: number
      success?: number
      failed?: number
      success_rate?: number
      failure_rate?: number
      avg_use_time?: number
      avg_first_response?: number
    }
    by_channel?: Array<{
      channel_id?: number
      channel_name?: string
      requests?: number
      failure_rate?: number
      avg_use_time?: number
    }>
  }
  alerts?: Array<{ level?: string; message?: string }>
  severity?: string
}

type SmokeKind = 'chat' | 'image' | 'grok-video'

type SmokeTarget = {
  kind: SmokeKind
  channel_id: number
  channel_name: string
  channel_type: number
  model: string
  endpoint_type: string
}

type SmokeSummary = {
  total: number
  success: number
  failed: number
  skipped: number
}

type SmokeResult = SmokeTarget & {
  success: boolean
  message: string
  error_code?: string
  time: number
}

type PendingCostlyTest = {
  types: string
  channelId?: number
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
  trade_no: string
  steps: AffiliateSmokeStep[]
}

const KIND_LABEL: Record<SmokeKind, string> = {
  chat: '对话',
  image: '图片',
  'grok-video': 'Grok Video',
}

const KIND_ICON = {
  chat: MessageSquare,
  image: Image,
  'grok-video': Video,
} satisfies Record<SmokeKind, typeof MessageSquare>

function pct(value?: number) {
  if (value === undefined || Number.isNaN(value)) return '-'
  return `${value.toFixed(1)}%`
}

function seconds(value?: number) {
  if (value === undefined || Number.isNaN(value)) return '-'
  return `${value.toFixed(2)}s`
}

function gib(value?: number) {
  if (value === undefined || Number.isNaN(value)) return '-'
  return `${value.toFixed(1)} GiB`
}

function integer(value?: number) {
  if (value === undefined || Number.isNaN(value)) return '-'
  return value.toLocaleString()
}

function statusVariant(success?: boolean): StatusVariant {
  if (success === undefined) return 'neutral'
  return success ? 'success' : 'danger'
}

function severityVariant(severity?: string): StatusVariant {
  switch ((severity || '').toLowerCase()) {
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

function severityText(severity?: string) {
  switch ((severity || '').toLowerCase()) {
    case 'critical':
      return '严重'
    case 'warning':
      return '警告'
    case 'normal':
      return '正常'
    default:
      return '未知'
  }
}

function getSmokeResultsByChannel(results: SmokeResult[]) {
  return results.reduce<
    Record<number, Partial<Record<SmokeKind, SmokeResult>>>
  >((acc, item) => {
    acc[item.channel_id] ??= {}
    acc[item.channel_id][item.kind] = item
    return acc
  }, {})
}

function getTargetsByChannel(targets: SmokeTarget[]) {
  return targets.reduce<Record<number, SmokeTarget[]>>((acc, item) => {
    acc[item.channel_id] ??= []
    acc[item.channel_id].push(item)
    return acc
  }, {})
}

async function getMonitorRuntime() {
  const res = await api.get<ApiEnvelope<RuntimeData>>('/api/monitor/runtime')
  return res.data.data ?? null
}

async function getMonitorSummary() {
  const res = await api.get<ApiEnvelope<MonitorSummary>>('/api/monitor/summary')
  return res.data.data ?? null
}

async function getSmokeTargets(types = 'chat,image,grok-video') {
  const res = await api.get<ApiEnvelope<never>>(
    '/api/channel/smoke-test/targets',
    {
      params: { types },
    }
  )
  return {
    summary: res.data.summary,
    targets: res.data.targets ?? [],
  }
}

async function runMonitorOnce() {
  const res = await api.post<ApiEnvelope<never>>('/api/monitor/run')
  return res.data
}

async function runAffiliateSmokeTest() {
  const res = await api.post<ApiEnvelope<AffiliateSmokeResult>>(
    '/api/monitor/affiliate-smoke-test'
  )
  return res.data.data ?? null
}

async function runSmokeTest(params: {
  types: string
  dryRun?: boolean
  channelId?: number
}) {
  const url = params.channelId
    ? `/api/channel/smoke-test/${params.channelId}`
    : '/api/channel/smoke-test'
  const res = await api.post<ApiEnvelope<never>>(url, undefined, {
    params: {
      types: params.types,
      dry_run: params.dryRun ? 'true' : undefined,
    },
  })
  return res.data
}

function MetricCard({
  title,
  value,
  hint,
  variant,
}: {
  title: string
  value: string
  hint?: string
  variant?: StatusVariant
}) {
  return (
    <div className='bg-background/60 rounded-lg border p-3'>
      <div className='flex items-center justify-between gap-3'>
        <div className='text-muted-foreground text-xs'>{title}</div>
        {variant && <StatusBadge variant={variant} copyable={false} />}
      </div>
      <div className='mt-2 text-xl font-semibold'>{value}</div>
      {hint && <div className='text-muted-foreground mt-1 text-xs'>{hint}</div>}
    </div>
  )
}

function SmokeResultBadge({ result }: { result?: SmokeResult }) {
  if (!result) {
    return (
      <StatusBadge
        variant='neutral'
        label='未测试'
        copyable={false}
        showDot={false}
      />
    )
  }
  return (
    <StatusBadge
      variant={statusVariant(result.success)}
      label={result.success ? `通过 ${seconds(result.time)}` : '失败'}
      copyText={result.message || `${result.kind} ${result.model}`}
    />
  )
}

export function SystemHealthSection() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [smokeResults, setSmokeResults] = useState<SmokeResult[]>([])
  const [affiliateSmokeResult, setAffiliateSmokeResult] =
    useState<AffiliateSmokeResult | null>(null)
  const [pendingCostlyTest, setPendingCostlyTest] =
    useState<PendingCostlyTest | null>(null)

  const runtimeQuery = useQuery({
    queryKey: ['openmind-monitor-runtime'],
    queryFn: getMonitorRuntime,
  })
  const summaryQuery = useQuery({
    queryKey: ['openmind-monitor-summary'],
    queryFn: getMonitorSummary,
  })
  const targetsQuery = useQuery({
    queryKey: ['channel-smoke-targets'],
    queryFn: () => getSmokeTargets(),
  })

  const monitorMutation = useMutation({
    mutationFn: runMonitorOnce,
    onSuccess: () => {
      toast.success(t('监控报告已发送'))
      queryClient.invalidateQueries({ queryKey: ['openmind-monitor-summary'] })
      queryClient.invalidateQueries({ queryKey: ['openmind-monitor-runtime'] })
    },
  })

  const smokeMutation = useMutation({
    mutationFn: runSmokeTest,
    onSuccess: (data) => {
      if (data.results) {
        setSmokeResults(data.results)
      }
      if (data.dry_run) {
        toast.success(t('测试目标预览已加载'))
      } else if (data.summary?.failed) {
        toast.error(t('自动化测试完成，但存在失败项'))
      } else {
        toast.success(t('自动化测试通过'))
      }
      queryClient.invalidateQueries({ queryKey: ['channel-smoke-targets'] })
    },
  })

  const affiliateSmokeMutation = useMutation({
    mutationFn: runAffiliateSmokeTest,
    onSuccess: (data) => {
      if (data) {
        setAffiliateSmokeResult(data)
        if (data.passed) {
          toast.success(t('分销闭环测试通过'))
        } else {
          toast.error(data.message || t('分销闭环测试未通过'))
        }
      } else {
        toast.error(t('分销闭环测试没有返回结果'))
      }
    },
  })

  const summary = summaryQuery.data
  const runtime = runtimeQuery.data ?? summary?.runtime
  const usageTotals = summary?.usage?.totals
  const targets = targetsQuery.data?.targets ?? []
  const targetsByChannel = useMemo(
    () => getTargetsByChannel(targets),
    [targets]
  )
  const resultsByChannel = useMemo(
    () => getSmokeResultsByChannel(smokeResults),
    [smokeResults]
  )
  const failedResults = smokeResults.filter((item) => !item.success)

  const refreshAll = () => {
    runtimeQuery.refetch()
    summaryQuery.refetch()
    targetsQuery.refetch()
  }

  const runCostly = (types: string, channelId?: number) => {
    setPendingCostlyTest({ types, channelId })
  }

  const confirmCostly = () => {
    if (!pendingCostlyTest) return
    smokeMutation.mutate(pendingCostlyTest)
    setPendingCostlyTest(null)
  }

  return (
    <div className='space-y-4'>
      <ConfirmDialog
        open={Boolean(pendingCostlyTest)}
        onOpenChange={(open) => {
          if (!open) setPendingCostlyTest(null)
        }}
        title={t('确认执行付费测试？')}
        desc={t(
          '图片和 Grok 视频测试会真实调用上游供应商，可能产生费用。建议只在部署验证或排查问题时执行。'
        )}
        cancelBtnText={t('取消')}
        confirmText={t('确认执行')}
        handleConfirm={confirmCostly}
        isLoading={smokeMutation.isPending}
      />
      <Card>
        <CardHeader>
          <CardTitle className='flex items-center gap-2'>
            <Server className='size-4' />
            {t('系统健康')}
          </CardTitle>
          <CardDescription>
            {t('查看服务器运行状态、用量告警和部署后的接口回归测试。')}
          </CardDescription>
          <CardAction className='flex gap-2'>
            <Button
              variant='outline'
              size='sm'
              onClick={refreshAll}
              disabled={
                runtimeQuery.isFetching ||
                summaryQuery.isFetching ||
                targetsQuery.isFetching
              }
            >
              <RefreshCcw className='size-4' />
              {t('刷新')}
            </Button>
            <Button
              size='sm'
              onClick={() => monitorMutation.mutate()}
              disabled={monitorMutation.isPending}
            >
              <Bell className='size-4' />
              {t('发送监控报告')}
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div className='grid gap-3 md:grid-cols-2 xl:grid-cols-4'>
            <MetricCard
              title={t('整体状态')}
              value={severityText(summary?.severity)}
              hint={
                runtime?.collected_at
                  ? `${t('采集时间')}: ${new Date(
                      runtime.collected_at * 1000
                    ).toLocaleString()}`
                  : undefined
              }
              variant={severityVariant(summary?.severity)}
            />
            <MetricCard
              title={t('请求数')}
              value={integer(usageTotals?.requests)}
              hint={`${t('失败')}: ${integer(usageTotals?.failed)}`}
              variant={
                (usageTotals?.failure_rate ?? 0) > 0 ? 'warning' : 'success'
              }
            />
            <MetricCard
              title={t('成功率')}
              value={pct(usageTotals?.success_rate)}
              hint={`${t('失败率')}: ${pct(usageTotals?.failure_rate)}`}
              variant={
                (usageTotals?.failure_rate ?? 0) > 5 ? 'danger' : 'success'
              }
            />
            <MetricCard
              title={t('延迟')}
              value={seconds(usageTotals?.avg_use_time)}
              hint={`${t('首包')}: ${seconds(usageTotals?.avg_first_response)}`}
              variant={
                (usageTotals?.avg_use_time ?? 0) > 20 ? 'warning' : 'success'
              }
            />
            <MetricCard
              title='CPU'
              value={pct(runtime?.cpu)}
              hint={
                runtime?.load
                  ? `Load ${runtime.load.load1 ?? '-'} / ${
                      runtime.load.load5 ?? '-'
                    } / ${runtime.load.load15 ?? '-'}`
                  : undefined
              }
              variant={(runtime?.cpu ?? 0) > 80 ? 'warning' : 'success'}
            />
            <MetricCard
              title={t('内存')}
              value={pct(runtime?.memory?.used_percent)}
              hint={`${gib(runtime?.memory?.used_gib)} / ${gib(
                runtime?.memory?.total_gib
              )}`}
              variant={
                (runtime?.memory?.used_percent ?? 0) > 80
                  ? 'warning'
                  : 'success'
              }
            />
            <MetricCard
              title={t('磁盘')}
              value={pct(runtime?.disk?.used_percent)}
              hint={`${runtime?.disk?.path ?? '/'}: ${gib(
                runtime?.disk?.used_gib
              )} / ${gib(runtime?.disk?.total_gib)}`}
              variant={
                (runtime?.disk?.used_percent ?? 0) > 80 ? 'warning' : 'success'
              }
            />
            <MetricCard
              title={t('告警')}
              value={integer(summary?.alerts?.length ?? 0)}
              hint={summary?.alerts?.[0]?.message}
              variant={
                (summary?.alerts?.length ?? 0) > 0 ? 'warning' : 'success'
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className='flex items-center gap-2'>
            <Activity className='size-4' />
            {t('自动化测试')}
          </CardTitle>
          <CardDescription>
            {t(
              '部署后检查各渠道接口是否能调通，图片和视频测试会真实调用上游。'
            )}
          </CardDescription>
          <CardAction className='flex flex-wrap justify-end gap-2'>
            <Button
              variant='outline'
              size='sm'
              onClick={() =>
                smokeMutation.mutate({ types: 'chat', dryRun: true })
              }
              disabled={smokeMutation.isPending}
            >
              <Eye className='size-4' />
              {t('预览')}
            </Button>
            <Button
              size='sm'
              onClick={() => smokeMutation.mutate({ types: 'chat' })}
              disabled={smokeMutation.isPending}
            >
              <MessageSquare className='size-4' />
              {t('安全测试')}
            </Button>
            <Button
              variant='outline'
              size='sm'
              onClick={() => runCostly('image,grok-video')}
              disabled={smokeMutation.isPending}
            >
              <Play className='size-4' />
              {t('付费测试')}
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div className='grid gap-3 md:grid-cols-4'>
            <MetricCard
              title={t('测试目标')}
              value={integer(
                targetsQuery.data?.summary?.total ?? targets.length
              )}
              hint={t('已启用渠道能力')}
            />
            <MetricCard
              title={t('通过')}
              value={integer(
                smokeResults.filter((item) => item.success).length
              )}
              variant='success'
            />
            <MetricCard
              title={t('失败')}
              value={integer(failedResults.length)}
              variant={failedResults.length > 0 ? 'danger' : 'success'}
            />
            <MetricCard
              title={t('最近执行')}
              value={smokeResults.length > 0 ? t('已完成') : t('未测试')}
              hint={
                smokeMutation.isPending
                  ? t('执行中...')
                  : t('结果会保留在当前页面直到刷新')
              }
              variant={smokeMutation.isPending ? 'warning' : 'neutral'}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className='flex items-center gap-2'>
            <GitBranch className='size-4' />
            {t('分销闭环测试')}
          </CardTitle>
          <CardDescription>
            {t(
              '模拟 A 邀请 B、B 邀请 C、C 充值，检查一级和二级返佣流水、奖励余额和重复执行幂等性。测试数据会自动回滚。'
            )}
          </CardDescription>
          <CardAction>
            <Button
              size='sm'
              onClick={() => affiliateSmokeMutation.mutate()}
              disabled={affiliateSmokeMutation.isPending}
            >
              <Users className='size-4' />
              {affiliateSmokeMutation.isPending
                ? t('执行中...')
                : t('执行分销测试')}
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className='space-y-4'>
          <div className='grid gap-3 md:grid-cols-2 xl:grid-cols-5'>
            <MetricCard
              title={t('结果')}
              value={
                affiliateSmokeResult
                  ? affiliateSmokeResult.passed
                    ? t('通过')
                    : t('失败')
                  : t('未测试')
              }
              hint={affiliateSmokeResult?.message}
              variant={
                affiliateSmokeResult
                  ? statusVariant(affiliateSmokeResult.passed)
                  : 'neutral'
              }
            />
            <MetricCard
              title={t('一级比例')}
              value={
                affiliateSmokeResult
                  ? `${affiliateSmokeResult.first_rate}%`
                  : '-'
              }
              hint={t('直接邀请人')}
            />
            <MetricCard
              title={t('二级比例')}
              value={
                affiliateSmokeResult
                  ? `${affiliateSmokeResult.second_rate}%`
                  : '-'
              }
              hint={t('上级邀请人')}
            />
            <MetricCard
              title={t('测试充值额度')}
              value={integer(affiliateSmokeResult?.gross_quota)}
              hint={t('事务回滚，不真实入账')}
            />
            <MetricCard
              title={t('预期佣金')}
              value={
                affiliateSmokeResult
                  ? `${integer(
                      affiliateSmokeResult.first_commission
                    )} / ${integer(affiliateSmokeResult.second_commission)}`
                  : '-'
              }
              hint={t('一级 / 二级')}
            />
          </div>

          {affiliateSmokeResult && (
            <div className='space-y-2'>
              <div className='text-muted-foreground text-xs'>
                {t('测试单号')}: {affiliateSmokeResult.trade_no}
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
                            'size-4',
                            step.passed ? 'text-success' : 'text-destructive'
                          )}
                        />
                        <span className='font-medium'>{step.name}</span>
                      </div>
                      {(step.expected || step.actual) && (
                        <div className='text-muted-foreground text-xs break-words'>
                          {step.expected && (
                            <span>
                              {t('预期')}: {step.expected}
                            </span>
                          )}
                          {step.expected && step.actual && (
                            <span className='mx-2'>/</span>
                          )}
                          {step.actual && (
                            <span>
                              {t('实际')}: {step.actual}
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
                      variant={statusVariant(step.passed)}
                      label={step.passed ? t('通过') : t('失败')}
                      copyable={false}
                    />
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t('渠道测试矩阵')}</CardTitle>
          <CardDescription>
            {t('按渠道和能力分别执行一键测试。')}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('渠道')}</TableHead>
                <TableHead>{t('能力')}</TableHead>
                <TableHead>{t('对话')}</TableHead>
                <TableHead>{t('图片')}</TableHead>
                <TableHead>{t('Grok Video')}</TableHead>
                <TableHead className='text-right'>{t('操作')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {Object.entries(targetsByChannel).map(([id, channelTargets]) => {
                const channelId = Number(id)
                const firstTarget = channelTargets[0]
                const targetKinds = new Set(
                  channelTargets.map((item) => item.kind)
                )
                const channelResults = resultsByChannel[channelId] ?? {}
                return (
                  <TableRow key={id}>
                    <TableCell>
                      <div className='font-medium'>
                        #{channelId} {firstTarget.channel_name || '-'}
                      </div>
                      <div className='text-muted-foreground text-xs'>
                        {t('类型')} {firstTarget.channel_type}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className='flex flex-wrap gap-1'>
                        {channelTargets.map((target) => {
                          const Icon = KIND_ICON[target.kind]
                          return (
                            <Badge
                              key={`${target.kind}-${target.model}`}
                              variant='outline'
                            >
                              <Icon className='size-3' />
                              {KIND_LABEL[target.kind]}
                            </Badge>
                          )
                        })}
                      </div>
                    </TableCell>
                    {(['chat', 'image', 'grok-video'] as SmokeKind[]).map(
                      (kind) => (
                        <TableCell key={kind}>
                          {targetKinds.has(kind) ? (
                            <SmokeResultBadge result={channelResults[kind]} />
                          ) : (
                            <span className='text-muted-foreground text-xs'>
                              -
                            </span>
                          )}
                        </TableCell>
                      )
                    )}
                    <TableCell>
                      <div className='flex justify-end gap-2'>
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() =>
                            smokeMutation.mutate({
                              types: 'chat',
                              channelId,
                            })
                          }
                          disabled={
                            smokeMutation.isPending || !targetKinds.has('chat')
                          }
                        >
                          <MessageSquare className='size-4' />
                        </Button>
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() => runCostly('image', channelId)}
                          disabled={
                            smokeMutation.isPending || !targetKinds.has('image')
                          }
                        >
                          <Image className='size-4' />
                        </Button>
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() => runCostly('grok-video', channelId)}
                          disabled={
                            smokeMutation.isPending ||
                            !targetKinds.has('grok-video')
                          }
                        >
                          <Video className='size-4' />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
              {targets.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className='text-muted-foreground h-24 text-center'
                  >
                    {targetsQuery.isLoading
                      ? t('加载中...')
                      : t('没有找到可测试的渠道')}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {smokeResults.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t('最近测试结果')}</CardTitle>
            <CardDescription>
              {t('显示最近一次按钮触发后的详细结果。')}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className='space-y-2'>
              {smokeResults.map((result) => {
                const Icon = result.success ? CheckCircle2 : XCircle
                return (
                  <div
                    key={`${result.channel_id}-${result.kind}-${result.model}`}
                    className={cn(
                      'flex items-start justify-between gap-3 rounded-lg border p-3',
                      result.success
                        ? 'border-success/30 bg-success/5'
                        : 'border-destructive/30 bg-destructive/5'
                    )}
                  >
                    <div className='min-w-0 space-y-1'>
                      <div className='flex flex-wrap items-center gap-2'>
                        <Icon
                          className={cn(
                            'size-4',
                            result.success ? 'text-success' : 'text-destructive'
                          )}
                        />
                        <span className='font-medium'>
                          #{result.channel_id} {result.channel_name || '-'}
                        </span>
                        <Badge variant='outline'>
                          {KIND_LABEL[result.kind]}
                        </Badge>
                        <span className='text-muted-foreground text-xs'>
                          {result.model}
                        </span>
                      </div>
                      {result.message && (
                        <div className='text-muted-foreground text-xs break-words'>
                          {result.message}
                        </div>
                      )}
                    </div>
                    <StatusBadge
                      variant={statusVariant(result.success)}
                      label={result.success ? seconds(result.time) : '失败'}
                      copyable={false}
                    />
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
