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
import { Link } from '@tanstack/react-router'
import { ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getLobeIcon } from '@/lib/lobe-icon'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { AnimateInView } from '@/components/animate-in-view'
import {
  DEFAULT_TOKEN_UNIT,
} from '@/features/pricing/constants'
import { usePricingData } from '@/features/pricing/hooks'
import { getDynamicPricingSummary } from '@/features/pricing/lib/dynamic-price'
import { stripTrailingZeros } from '@/features/pricing/lib/price'
import type {
  PricingModel,
  PricingVendor,
} from '@/features/pricing/types'

const MODEL_LIMIT = 12
const MEDIA_MODEL_LIMIT = 8
const PLATFORM_PRICE_MULTIPLIER = 6
const CURRENT_SALE_MULTIPLIER = 3
const MEDIA_ENDPOINTS = new Set(['image-generation', 'openai-video'])

const preferredVendorOrder = [
  'OpenAI',
  'Anthropic',
  'Google',
  'xAI',
  'DeepSeek',
]

const preferredModelOrder = [
  'gpt-5.6-sol',
  'gpt-5.5',
  'gpt-5.4',
  'gpt-5.4-mini',
  'gpt-5.3-codex-spark',
  'claude-fable-5',
  'claude-opus-4-8',
  'claude-opus-4-7',
  'claude-opus-4-6',
  'claude-sonnet-5',
  'claude-sonnet-4-6',
  'claude-haiku-4-5-20251001',
]

const preferredMediaModelOrder = [
  'gpt-image-2',
  'gemini-3-pro-image-preview',
  'gemini-3.1-flash-image-preview',
  'grok-imagine-video-1.5-preview',
  'grok-1.5-video-10s',
  'grok-1.5-video-15s',
  'grok-video-1.5-preview',
  'grok-imagine-1.0-video',
]

const officialPriceRows = [
  {
    model: 'gpt-5.6-sol',
    vendor: 'OpenAI',
    context: '1M',
    official: { input: 5, output: 30, cache: 0.5 },
    cost: { input: 0.8, output: 4.8, cache: 0.08 },
  },
  {
    model: 'gpt-5.5',
    vendor: 'OpenAI',
    context: '1M',
    official: { input: 5, output: 30, cache: 0.5 },
    cost: { input: 0.8, output: 4.8, cache: 0.08 },
  },
  {
    model: 'gpt-5.4',
    vendor: 'OpenAI',
    context: '1M',
    official: { input: 2.5, output: 15, cache: 0.25 },
    cost: { input: 0.4, output: 2.4, cache: 0.04 },
  },
  {
    model: 'gpt-5.4-mini',
    vendor: 'OpenAI',
    context: '1M',
    official: { input: 0.75, output: 4.5, cache: 0.075 },
    cost: { input: 0.12, output: 0.72, cache: 0.012 },
  },
  {
    model: 'gpt-5.3-codex-spark',
    vendor: 'OpenAI',
    context: '1M',
    official: { input: 1.75, output: 14, cache: 0.175 },
    cost: { input: 0.28, output: 2.24, cache: 0.028 },
  },
  {
    model: 'claude-fable-5',
    vendor: 'Anthropic',
    context: '1M',
    official: { input: 10, output: 50, cache: 1 },
    cost: { input: 1.1, output: 5.5, cache: 0.11 },
  },
  {
    model: 'claude-opus-4-8',
    vendor: 'Anthropic',
    context: '1M',
    official: { input: 5, output: 25, cache: 0.5 },
    cost: { input: 0.55, output: 2.75, cache: 0.0275 },
  },
  {
    model: 'claude-opus-4-7',
    vendor: 'Anthropic',
    context: '1M',
    official: { input: 5, output: 25, cache: 0.5 },
    cost: { input: 0.55, output: 2.75, cache: 0.055 },
  },
  {
    model: 'claude-opus-4-6',
    vendor: 'Anthropic',
    context: '1M',
    official: { input: 5, output: 25, cache: 0.5 },
    cost: { input: 0.55, output: 2.75, cache: 0.055 },
  },
  {
    model: 'claude-sonnet-5',
    vendor: 'Anthropic',
    context: '1M',
    official: { input: 2, output: 10, cache: 0.2 },
    cost: { input: 0.33, output: 1.65, cache: 0.033 },
  },
  {
    model: 'claude-sonnet-4-6',
    vendor: 'Anthropic',
    context: '1M',
    official: { input: 3, output: 15, cache: 0.3 },
    cost: { input: 0.33, output: 1.65, cache: 0.033 },
  },
  {
    model: 'claude-haiku-4-5-20251001',
    vendor: 'Anthropic',
    context: '1M',
    official: { input: 1, output: 5, cache: 0.1 },
    cost: { input: 0.11, output: 0.55, cache: 0.011 },
  },
] as const

type VendorFilter = 'all' | string

interface VendorOption {
  id: VendorFilter
  name: string
  icon?: string
}

type OfficialPriceRow = (typeof officialPriceRows)[number]

interface MediaPriceRow {
  model: PricingModel
  type: 'image' | 'video'
  priceLabel: string
  billingLabel: string
}

function isHomepageMediaModel(model: PricingModel) {
  const endpoints = model.supported_endpoint_types || []
  return endpoints.some((endpoint) => MEDIA_ENDPOINTS.has(endpoint))
}

function getVendorSortValue(vendorName?: string) {
  if (!vendorName) return Number.MAX_SAFE_INTEGER
  const index = preferredVendorOrder.findIndex((name) => name === vendorName)
  return index === -1 ? Number.MAX_SAFE_INTEGER : index
}

function getMediaModelSortValue(modelName: string) {
  const index = preferredMediaModelOrder.findIndex((name) => name === modelName)
  return index === -1 ? Number.MAX_SAFE_INTEGER : index
}

function getModelSortValue(modelName: string) {
  const index = preferredModelOrder.findIndex((name) => name === modelName)
  return index === -1 ? Number.MAX_SAFE_INTEGER : index
}

function getVendorOptions(
  rows: OfficialPriceRow[],
  vendors: PricingVendor[],
  allLabel: string
): VendorOption[] {
  const visibleVendorNames = Array.from(
    new Set(rows.map((row) => row.vendor))
  )
  const vendorMap = new Map(vendors.map((vendor) => [vendor.name, vendor]))

  const options = visibleVendorNames
    .sort((a, b) => {
      const vendorOrder = getVendorSortValue(a) - getVendorSortValue(b)
      if (vendorOrder !== 0) return vendorOrder
      return a.localeCompare(b)
    })
    .map((vendorName) => ({
      id: vendorName,
      name: vendorName,
      icon: vendorMap.get(vendorName)?.icon,
    }))

  return [{ id: 'all', name: allLabel }, ...options]
}

function getOfficialVendorIcon(
  officialVendor: string,
  vendors: PricingVendor[]
) {
  return vendors.find((vendor) => vendor.name === officialVendor)?.icon
}

function getOfficialVendorOption(
  officialVendor: string,
  vendors: PricingVendor[]
) {
  const icon = getOfficialVendorIcon(officialVendor, vendors)
  return {
    name: officialVendor,
    icon,
  }
}

function getMinGroupRatio(model: PricingModel) {
  const enableGroups = Array.isArray(model.enable_groups)
    ? model.enable_groups
    : []
  const groupRatio = model.group_ratio || {}

  if (enableGroups.length === 0) return 1

  const ratios = enableGroups
    .map((group) => groupRatio[group])
    .filter((ratio) => Number.isFinite(Number(ratio)))
    .map(Number)

  return ratios.length > 0 ? Math.min(...ratios) : 1
}

function formatFixedCurrency(symbol: '$' | '\uffe5', value: number) {
  if (!Number.isFinite(value)) return '-'

  const digits = Math.abs(value) >= 1 ? 4 : 6
  const formatted = value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  })

  return stripTrailingZeros(`${symbol}${formatted}`)
}

function getCurrentSaleTokenPrice(
  row: OfficialPriceRow,
  type: 'input' | 'output' | 'cache',
) {
  return row.cost[type] * CURRENT_SALE_MULTIPLIER
}

function getPlatformRequestPrice(value: number, usdExchangeRate: number) {
  return (value * PLATFORM_PRICE_MULTIPLIER) / usdExchangeRate
}

function formatOfficialPrice(value: number) {
  return formatFixedCurrency('$', value)
}

function formatPlatformPrice(value: number) {
  return formatFixedCurrency('$', value)
}

function getMediaPriceEntries(model: PricingModel, usdExchangeRate: number) {
  const dynamicSummary = getDynamicPricingSummary(model, {
    tokenUnit: DEFAULT_TOKEN_UNIT,
    priceRate: PLATFORM_PRICE_MULTIPLIER,
    usdExchangeRate,
    groupRatioMultiplier: getMinGroupRatio(model),
  })

  const fixedEntries = dynamicSummary?.fixedRequestEntries || []
  if (fixedEntries.length > 0) {
    return fixedEntries.map((entry) => ({
      label: entry.label,
      price: getPlatformRequestPrice(entry.value, usdExchangeRate),
    }))
  }

  const price = Number(model.model_price)
  if (Number.isFinite(price) && price > 0) {
    return [
      {
        label: 'base',
        price: getPlatformRequestPrice(
          price * getMinGroupRatio(model),
          usdExchangeRate
        ),
      },
    ]
  }

  return []
}

function formatMediaPrice(entries: Array<{ label: string; price: number }>) {
  if (entries.length === 0) return '-'
  return entries
    .map((entry) => {
      const label =
        entry.label && entry.label !== 'base' ? `${entry.label} ` : ''
      return `${label}${formatPlatformPrice(entry.price)}`
    })
    .join(' / ')
}

function getMediaBillingLabel(
  entries: Array<{ label: string; price: number }>,
  t: (key: string) => string
) {
  if (entries.length > 1) return t('Tiered pricing')
  return t('Fixed request price')
}

function stripNumberZeros(value: string) {
  return value.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')
}

function formatDiscountRatio(
  value: number,
  language: string | undefined,
  aboveOfficialLabel: string
) {
  if (!Number.isFinite(value) || value <= 0) return '-'
  if (value > 1) return aboveOfficialLabel

  if (language?.startsWith('zh')) {
    return `${stripNumberZeros((value * 10).toFixed(1))}\u6298`
  }

  return `${Math.round(value * 100)}%`
}

function PriceText(props: {
  value: string
  muted?: boolean
  crossed?: boolean
  accent?: boolean
}) {
  return (
    <span
      className={cn(
        'font-mono text-sm font-semibold tabular-nums',
        props.muted ? 'text-muted-foreground' : 'text-foreground',
        props.crossed && 'text-muted-foreground line-through',
        props.accent && 'text-rose-700 dark:text-rose-200'
      )}
    >
      {props.value}
    </span>
  )
}

function PriceCell(props: {
  value: string
  cacheLabel?: string
  crossed?: boolean
  accent?: boolean
}) {
  return (
    <div className='space-y-1'>
      <PriceText
        value={props.value}
        crossed={props.crossed}
        accent={props.accent}
      />
      {props.cacheLabel ? (
        <div className='text-muted-foreground text-xs leading-5'>
          {props.cacheLabel}
        </div>
      ) : null}
    </div>
  )
}

function VendorChip(props: {
  option: VendorOption
  active: boolean
  onClick: () => void
}) {
  const icon = props.option.icon ? getLobeIcon(props.option.icon, 16) : null

  return (
    <button
      type='button'
      onClick={props.onClick}
      className={cn(
        'border-border bg-background hover:bg-muted inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-sm font-medium transition-colors',
        props.active &&
          'border-rose-400 bg-rose-50 text-rose-700 dark:border-rose-500/70 dark:bg-rose-950/30 dark:text-rose-200'
      )}
    >
      {icon}
      {props.option.name}
    </button>
  )
}

export function ModelPriceComparison() {
  const { t, i18n } = useTranslation()
  const { models, vendors, isLoading, usdExchangeRate } = usePricingData()
  const [activeVendor, setActiveVendor] = useState<VendorFilter>('all')

  const comparisonRows = useMemo(() => {
    return [...officialPriceRows]
      .sort((a, b) => {
        const vendorOrder = getVendorSortValue(a.vendor) - getVendorSortValue(b.vendor)
        if (vendorOrder !== 0) return vendorOrder

        const modelOrder = getModelSortValue(a.model) - getModelSortValue(b.model)
        if (modelOrder !== 0) return modelOrder

        return a.model.localeCompare(b.model)
      })
  }, [])

  const vendorOptions = useMemo(
    () => getVendorOptions(comparisonRows, vendors, t('All')),
    [comparisonRows, t, vendors]
  )

  const visibleRows = useMemo(() => {
    const filtered =
      activeVendor === 'all'
        ? comparisonRows
        : comparisonRows.filter((row) => row.vendor === activeVendor)

    return filtered.slice(0, MODEL_LIMIT)
  }, [activeVendor, comparisonRows])

  const mediaRows = useMemo(() => {
    return models
      .filter(isHomepageMediaModel)
      .map((model): MediaPriceRow | null => {
        const endpoints = model.supported_endpoint_types || []
        const type = endpoints.includes('openai-video') ? 'video' : 'image'
        const entries = getMediaPriceEntries(model, usdExchangeRate)
        if (entries.length === 0) return null
        return {
          model,
          type,
          priceLabel: formatMediaPrice(entries),
          billingLabel: getMediaBillingLabel(entries, t),
        }
      })
      .flatMap((row) => (row ? [row] : []))
      .sort((a, b) => {
        const typeOrder = a.type.localeCompare(b.type)
        if (typeOrder !== 0) return typeOrder
        const modelOrder =
          getMediaModelSortValue(a.model.model_name) -
          getMediaModelSortValue(b.model.model_name)
        if (modelOrder !== 0) return modelOrder
        return a.model.model_name.localeCompare(b.model.model_name)
      })
      .slice(0, MEDIA_MODEL_LIMIT)
  }, [models, t, usdExchangeRate])

  return (
    <section className='relative z-10 px-4 py-16 sm:px-6 md:py-24'>
      <div className='mx-auto max-w-7xl'>
        <AnimateInView className='mx-auto mb-8 max-w-3xl text-center md:mb-10'>
          <p className='text-muted-foreground mb-3 text-[11px] font-semibold tracking-[0.12em] uppercase'>
            {t('Model Price Comparison')}
          </p>
          <h2 className='text-3xl leading-tight font-bold md:text-4xl'>
            {t('Compare official rates with OpenMind API rates')}
          </h2>
          <p className='text-muted-foreground mx-auto mt-4 max-w-2xl text-sm leading-7 md:text-base'>
            {t(
              'Popular model prices are shown side by side, with input, output, and cache rates per 1M tokens.'
            )}
          </p>
        </AnimateInView>

        <AnimateInView delay={80} className='space-y-4'>
          <div className='flex flex-wrap items-center justify-center gap-2'>
            {vendorOptions.map((option) => (
              <VendorChip
                key={option.id}
                option={option}
                active={activeVendor === option.id}
                onClick={() => setActiveVendor(option.id)}
              />
            ))}
          </div>

          <div className='bg-background overflow-hidden rounded-lg border border-rose-200/80 shadow-sm dark:border-rose-900/60'>
            <Table className='min-w-[1180px]'>
              <TableHeader>
                <TableRow className='hover:bg-transparent'>
                  <TableHead className='bg-muted/60 w-[270px] px-4 py-3 text-xs font-bold'>
                    {t('Model')}
                  </TableHead>
                  <TableHead className='bg-muted/60 w-[150px] px-4 py-3 text-xs font-bold'>
                    {t('Vendor')}
                  </TableHead>
                  <TableHead className='bg-muted/60 w-[120px] px-4 py-3 text-xs font-bold'>
                    {t('Context')}
                  </TableHead>
                  <TableHead className='bg-muted/60 w-[160px] px-4 py-3 text-xs font-bold'>
                    {t('Input (Official)')}
                  </TableHead>
                  <TableHead className='bg-muted/60 w-[160px] px-4 py-3 text-xs font-bold'>
                    {t('Output (Official)')}
                  </TableHead>
                  <TableHead className='w-[180px] bg-rose-50 px-4 py-3 text-xs font-bold text-rose-700 dark:bg-rose-950/20 dark:text-rose-200'>
                    {t('Input (OpenMind API)')}
                  </TableHead>
                  <TableHead className='w-[180px] bg-rose-50 px-4 py-3 text-xs font-bold text-rose-700 dark:bg-rose-950/20 dark:text-rose-200'>
                    {t('Output (OpenMind API)')}
                  </TableHead>
                  <TableHead className='w-[120px] bg-rose-50 px-4 py-3 text-xs font-bold text-rose-700 dark:bg-rose-950/20 dark:text-rose-200'>
                    {t('Discount Ratio')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visibleRows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className='h-28 text-center'>
                      <span className='text-muted-foreground text-sm'>
                        {t('No configured model prices available.')}
                      </span>
                    </TableCell>
                  </TableRow>
                ) : (
                  visibleRows.map((row) => {
                    const officialVendor = getOfficialVendorOption(
                      row.vendor,
                      vendors
                    )
                    const currentSaleInput = getCurrentSaleTokenPrice(
                      row,
                      'input'
                    )
                    const currentSaleOutput = getCurrentSaleTokenPrice(
                      row,
                      'output'
                    )
                    const currentSaleCache = getCurrentSaleTokenPrice(
                      row,
                      'cache'
                    )
                    const discountRatio =
                      currentSaleInput / row.official.input

                    return (
                      <TableRow
                        key={row.model}
                        className='hover:bg-muted/25'
                      >
                        <TableCell className='px-4 py-4 font-mono text-sm font-semibold'>
                          {row.model}
                        </TableCell>
                        <TableCell className='px-4 py-4 text-sm font-medium'>
                          <div className='flex items-center gap-2'>
                            {officialVendor.icon
                              ? getLobeIcon(officialVendor.icon, 16)
                              : null}
                            {officialVendor.name}
                          </div>
                        </TableCell>
                        <TableCell className='text-muted-foreground px-4 py-4 text-sm font-medium'>
                          {row.context}
                        </TableCell>
                        <TableCell className='bg-muted/20 px-4 py-4'>
                          <PriceCell
                            value={formatOfficialPrice(row.official.input)}
                            crossed
                            cacheLabel={`${t('Cache')} ${formatOfficialPrice(row.official.cache)}`}
                          />
                        </TableCell>
                        <TableCell className='bg-muted/20 px-4 py-4'>
                          <PriceCell
                            value={formatOfficialPrice(row.official.output)}
                            crossed
                          />
                        </TableCell>
                        <TableCell className='bg-rose-50/40 px-4 py-4 dark:bg-rose-950/10'>
                          <PriceCell
                            value={formatPlatformPrice(currentSaleInput)}
                            accent
                            cacheLabel={`${t('Cache')} ${formatPlatformPrice(currentSaleCache)}`}
                          />
                        </TableCell>
                        <TableCell className='bg-rose-50/40 px-4 py-4 dark:bg-rose-950/10'>
                          <PriceCell
                            value={formatPlatformPrice(currentSaleOutput)}
                            accent
                          />
                        </TableCell>
                        <TableCell className='bg-rose-50/40 px-4 py-4 dark:bg-rose-950/10'>
                          <span
                            className={cn(
                              'inline-flex items-center rounded-full px-2.5 py-1 text-xs font-bold tabular-nums',
                              discountRatio <= 1
                                ? 'bg-rose-100 text-rose-700 dark:bg-rose-950/50 dark:text-rose-200'
                                : 'bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-200'
                            )}
                          >
                            {formatDiscountRatio(
                              discountRatio,
                              i18n.resolvedLanguage || i18n.language,
                              t('Above official')
                            )}
                          </span>
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>

            {!isLoading && mediaRows.length > 0 ? (
              <div className='border-border/70 border-t'>
                <div className='flex flex-col gap-1 px-4 pt-4 pb-2'>
                  <h3 className='text-sm font-bold'>
                    {t('Image')} / {t('Video')} · {t('Price')}
                  </h3>
                </div>
                <Table className='min-w-[900px]'>
                  <TableHeader>
                    <TableRow className='hover:bg-transparent'>
                      <TableHead className='bg-muted/40 w-[300px] px-4 py-3 text-xs font-bold'>
                        {t('Model')}
                      </TableHead>
                      <TableHead className='bg-muted/40 w-[160px] px-4 py-3 text-xs font-bold'>
                        {t('Vendor')}
                      </TableHead>
                      <TableHead className='bg-muted/40 w-[120px] px-4 py-3 text-xs font-bold'>
                        {t('Type')}
                      </TableHead>
                      <TableHead className='w-[260px] bg-rose-50 px-4 py-3 text-xs font-bold text-rose-700 dark:bg-rose-950/20 dark:text-rose-200'>
                        {t('Price')} (OpenMind API)
                      </TableHead>
                      <TableHead className='bg-muted/40 px-4 py-3 text-xs font-bold'>
                        {t('Billing')}
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {mediaRows.map((row) => {
                      const vendor = vendors.find(
                        (item) => item.id === row.model.vendor_id
                      )
                      const typeLabel =
                        row.type === 'video' ? t('Video') : t('Image')
                      return (
                        <TableRow
                          key={row.model.model_name}
                          className='hover:bg-muted/25'
                        >
                          <TableCell className='px-4 py-4 font-mono text-sm font-semibold'>
                            {row.model.model_name}
                          </TableCell>
                          <TableCell className='px-4 py-4 text-sm font-medium'>
                            <div className='flex items-center gap-2'>
                              {vendor?.icon
                                ? getLobeIcon(vendor.icon, 16)
                                : null}
                              {vendor?.name || '-'}
                            </div>
                          </TableCell>
                          <TableCell className='text-muted-foreground px-4 py-4 text-sm font-medium'>
                            {typeLabel}
                          </TableCell>
                          <TableCell className='bg-rose-50/40 px-4 py-4 dark:bg-rose-950/10'>
                            <PriceText value={row.priceLabel} accent />
                          </TableCell>
                          <TableCell className='text-muted-foreground px-4 py-4 text-sm'>
                            {row.billingLabel}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            ) : null}

            <div className='border-border/70 flex justify-end border-t px-4 py-4'>
              <div className='flex flex-wrap items-center gap-2'>
                <span className='text-muted-foreground text-sm'>
                  {t('View more models')}:
                </span>
                {vendorOptions.slice(1).map((vendor) => (
                  <Button
                    key={vendor.id}
                    variant='outline'
                    size='sm'
                    render={<Link to='/pricing' />}
                  >
                    {vendor.icon ? getLobeIcon(vendor.icon, 14) : null}
                    {vendor.name}
                  </Button>
                ))}
                <Button size='sm' render={<Link to='/pricing' />}>
                  {t('Model Catalog')}
                  <ArrowRight className='size-3.5' />
                </Button>
              </div>
            </div>
          </div>
        </AnimateInView>
      </div>
    </section>
  )
}
