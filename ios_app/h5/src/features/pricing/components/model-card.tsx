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
import { memo } from 'react'
import { ChevronRight, Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getLobeIcon } from '@/lib/lobe-icon'
import { cn } from '@/lib/utils'
import { useCopyToClipboard } from '@/hooks/use-copy-to-clipboard'
import { StatusBadge } from '@/components/status-badge'
import { DEFAULT_TOKEN_UNIT } from '../constants'
import {
  formatDynamicUnitPrice,
  getDynamicDisplayGroupRatio,
  getDynamicPricingSummary,
} from '../lib/dynamic-price'
import { parseTags } from '../lib/filters'
import { getGroupRatioBounds, isTokenBasedModel } from '../lib/model-helpers'
import {
  formatPerSecondPrice,
  parsePerSecondBillingExpr,
} from '../lib/per-second-pricing'
import {
  formatFixedPrice,
  formatGroupPrice,
  formatPrice,
  formatRequestPrice,
  stripTrailingZeros,
} from '../lib/price'
import type { PricingModel, TokenUnit } from '../types'
import { ModelPerfBadge, type ModelPerfBadgeData } from './model-perf-badge'

export interface ModelCardProps {
  model: PricingModel
  onClick: () => void
  priceRate?: number
  usdExchangeRate?: number
  tokenUnit?: TokenUnit
  showRechargePrice?: boolean
  focusedGroup?: string
  perf?: ModelPerfBadgeData
}

export const ModelCard = memo(function ModelCard(props: ModelCardProps) {
  const { t } = useTranslation()
  const { copyToClipboard } = useCopyToClipboard()
  const tokenUnit = props.tokenUnit ?? DEFAULT_TOKEN_UNIT
  const priceRate = props.priceRate ?? 1
  const usdExchangeRate = props.usdExchangeRate ?? 1
  const showRechargePrice = props.showRechargePrice ?? false
  const isTokenBased = isTokenBasedModel(props.model)
  const tokenUnitLabel = tokenUnit === 'K' ? '1K' : '1M'
  const groupRatioBounds = getGroupRatioBounds(props.model)
  const tags = parseTags(props.model.tags)
  const groups = props.model.enable_groups || []
  const focusedGroup =
    props.focusedGroup && groups.includes(props.focusedGroup)
      ? props.focusedGroup
      : undefined
  const focusedGroupRatio =
    focusedGroup !== undefined ? props.model.group_ratio?.[focusedGroup] ?? 1 : 1
  const hasGroupPriceVariance =
    focusedGroup === undefined && groupRatioBounds.hasMultiple
  const endpoints = props.model.supported_endpoint_types || []
  const vendorIcon = props.model.vendor_icon
    ? getLobeIcon(props.model.vendor_icon, 28)
    : null
  const initial = props.model.model_name?.charAt(0).toUpperCase() || '?'
  const isDynamicPricing =
    props.model.billing_mode === 'tiered_expr' &&
    Boolean(props.model.billing_expr)
  const perSecondPrice = isDynamicPricing
    ? parsePerSecondBillingExpr(props.model.billing_expr || '')
    : null
  const hasCachedPrice = isTokenBased && props.model.cache_ratio != null
  const dynamicSummary = isDynamicPricing
    ? getDynamicPricingSummary(props.model, {
        tokenUnit,
        showRechargePrice,
        priceRate,
        usdExchangeRate,
        groupRatioMultiplier:
          focusedGroup !== undefined
            ? focusedGroupRatio
            : getDynamicDisplayGroupRatio(props.model),
      })
    : null
  const displayGroupRatioMap = {
    __min: groupRatioBounds.min,
    __max: groupRatioBounds.max,
  }

  const formatRange = (minValue: string, maxValue: string) => {
    const start = stripTrailingZeros(minValue)
    const end = stripTrailingZeros(maxValue)
    return start === end ? start : `${start} - ${end}`
  }

  const formatDynamicEntryValue = (value: number) => {
    if (!hasGroupPriceVariance) {
      return stripTrailingZeros(
        formatDynamicUnitPrice(value, {
          tokenUnit,
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          groupRatioMultiplier:
            focusedGroup !== undefined
              ? focusedGroupRatio
              : getDynamicDisplayGroupRatio(props.model),
        })
      )
    }

    return formatRange(
      formatDynamicUnitPrice(value, {
        tokenUnit,
        showRechargePrice,
        priceRate,
        usdExchangeRate,
        groupRatioMultiplier: groupRatioBounds.min,
      }),
      formatDynamicUnitPrice(value, {
        tokenUnit,
        showRechargePrice,
        priceRate,
        usdExchangeRate,
        groupRatioMultiplier: groupRatioBounds.max,
      })
    )
  }

  const formatTieredPerSecondValue = (value: number) => {
    if (!hasGroupPriceVariance) {
      return formatPerSecondPrice(value, {
        showRechargePrice,
        priceRate,
        usdExchangeRate,
        groupRatioMultiplier:
          focusedGroup !== undefined
            ? focusedGroupRatio
            : getDynamicDisplayGroupRatio(props.model),
      })
    }

    return formatRange(
      formatPerSecondPrice(value, {
        showRechargePrice,
        priceRate,
        usdExchangeRate,
        groupRatioMultiplier: groupRatioBounds.min,
      }),
      formatPerSecondPrice(value, {
        showRechargePrice,
        priceRate,
        usdExchangeRate,
        groupRatioMultiplier: groupRatioBounds.max,
      })
    )
  }

  const formatTokenPriceForCard = (type: 'input' | 'output' | 'cache') => {
    if (focusedGroup !== undefined) {
      return stripTrailingZeros(
        formatGroupPrice(
          props.model,
          focusedGroup,
          type,
          tokenUnit,
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          props.model.group_ratio || {}
        )
      )
    }

    if (hasGroupPriceVariance) {
      return formatRange(
        formatGroupPrice(
          props.model,
          '__min',
          type,
          tokenUnit,
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          displayGroupRatioMap
        ),
        formatGroupPrice(
          props.model,
          '__max',
          type,
          tokenUnit,
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          displayGroupRatioMap
        )
      )
    }

    return formatPrice(
      props.model,
      type,
      tokenUnit,
      showRechargePrice,
      priceRate,
      usdExchangeRate
    )
  }

  const formatRequestPriceForCard = () => {
    if (focusedGroup !== undefined) {
      return stripTrailingZeros(
        formatFixedPrice(
          props.model,
          focusedGroup,
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          props.model.group_ratio || {}
        )
      )
    }

    if (hasGroupPriceVariance) {
      return formatRange(
        formatFixedPrice(
          props.model,
          '__min',
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          displayGroupRatioMap
        ),
        formatFixedPrice(
          props.model,
          '__max',
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          displayGroupRatioMap
        )
      )
    }

    return formatRequestPrice(
      props.model,
      showRechargePrice,
      priceRate,
      usdExchangeRate
    )
  }

  const primaryGroup = focusedGroup ?? groups[0]
  const perSecondFormatted =
    perSecondPrice !== null
      ? hasGroupPriceVariance
        ? formatRange(
            formatPerSecondPrice(Number(perSecondPrice), {
              showRechargePrice,
              priceRate,
              usdExchangeRate,
              groupRatioMultiplier: groupRatioBounds.min,
            }),
            formatPerSecondPrice(Number(perSecondPrice), {
              showRechargePrice,
              priceRate,
              usdExchangeRate,
              groupRatioMultiplier: groupRatioBounds.max,
            })
          )
        : formatPerSecondPrice(Number(perSecondPrice), {
            showRechargePrice,
            priceRate,
            usdExchangeRate,
            groupRatioMultiplier:
              focusedGroup !== undefined
                ? focusedGroupRatio
                : getDynamicDisplayGroupRatio(props.model),
          })
      : null
  const bottomTags = [...endpoints.slice(0, 2), ...tags.slice(0, 2)]
  const hiddenCount =
    Math.max(groups.length - 1, 0) +
    Math.max(endpoints.length - 2, 0) +
    Math.max(tags.length - 2, 0)

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    copyToClipboard(props.model.model_name || '')
  }

  return (
    <div
      className={cn(
        'group relative flex flex-col rounded-xl border p-3 transition-colors sm:p-5',
        'hover:bg-muted/20'
      )}
    >
      {/* Header: icon + name + price + actions */}
      <div className='flex items-start justify-between gap-2.5 sm:gap-3'>
        <div className='flex min-w-0 items-start gap-2.5 sm:gap-3'>
          <div className='bg-muted/40 flex size-9 shrink-0 items-center justify-center rounded-lg sm:size-10 sm:rounded-xl'>
            {vendorIcon || (
              <span className='text-muted-foreground text-sm font-bold'>
                {initial}
              </span>
            )}
          </div>
          <div className='min-w-0'>
            <h3 className='text-foreground truncate font-mono text-[15px] leading-tight font-bold'>
              {props.model.model_name}
            </h3>
            <div className='mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs sm:mt-1 sm:gap-x-3'>
              {perSecondFormatted ? (
                <span className='text-muted-foreground whitespace-nowrap'>
                  <span className='text-foreground font-mono font-semibold'>
                    {perSecondFormatted}
                  </span>{' '}
                  / {t('second')}
                </span>
              ) : dynamicSummary ? (
                dynamicSummary.isPerSecondTierPricing ? (
                  <>
                    {dynamicSummary.perSecondEntries.map((entry) => (
                      <span
                        key={entry.label || entry.value}
                        className='text-muted-foreground whitespace-nowrap'
                      >
                        {entry.label || t('Default')}{' '}
                        <span className='text-foreground font-mono font-semibold'>
                          {formatTieredPerSecondValue(entry.value)}
                        </span>
                        /{t('second')}
                      </span>
                    ))}
                  </>
                ) : dynamicSummary.isSpecialExpression ? (
                  <span className='min-w-0'>
                    <span className='text-amber-700 dark:text-amber-300'>
                      {t('Special billing expression')}
                    </span>
                    <code className='text-muted-foreground/70 mt-0.5 line-clamp-1 block font-mono text-[11px] break-all'>
                      {dynamicSummary.rawExpression}
                    </code>
                  </span>
                ) : dynamicSummary.primaryEntries.length > 0 ? (
                  <>
                    {dynamicSummary.primaryEntries.map((entry) => (
                      <span
                        key={entry.key}
                        className='text-muted-foreground whitespace-nowrap'
                      >
                        {t(entry.shortLabel)}{' '}
                        <span className='text-foreground font-mono font-semibold'>
                          {formatDynamicEntryValue(entry.value)}
                        </span>
                        /{tokenUnitLabel}
                      </span>
                    ))}
                  </>
                ) : (
                  <span className='text-muted-foreground text-xs'>
                    {t('Dynamic Pricing')}
                  </span>
                )
              ) : isTokenBased ? (
                <>
                  <span className='text-muted-foreground whitespace-nowrap'>
                    {t('Input')}{' '}
                    <span className='text-foreground font-mono font-semibold'>
                      {formatTokenPriceForCard('input')}
                    </span>
                    /{tokenUnitLabel}
                  </span>
                  <span className='text-muted-foreground whitespace-nowrap'>
                    {t('Output')}{' '}
                    <span className='text-foreground font-mono font-semibold'>
                      {formatTokenPriceForCard('output')}
                    </span>
                    /{tokenUnitLabel}
                  </span>
                  {hasCachedPrice && (
                    <span className='text-muted-foreground/60 whitespace-nowrap'>
                      {t('Cached')}{' '}
                      <span className='font-mono'>
                        {formatTokenPriceForCard('cache')}
                      </span>
                    </span>
                  )}
                </>
              ) : (
                <span className='text-muted-foreground whitespace-nowrap'>
                  <span className='text-foreground font-mono font-semibold'>
                    {formatRequestPriceForCard()}
                  </span>{' '}
                  / {t('request')}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className='flex shrink-0 items-center gap-1.5'>
          <button
            type='button'
            onClick={props.onClick}
            className='text-muted-foreground hover:text-foreground hover:bg-muted inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors sm:px-2.5 sm:py-1.5'
          >
            {t('Details')}
            <ChevronRight className='size-3.5' />
          </button>
          <button
            type='button'
            onClick={handleCopy}
            className='text-muted-foreground hover:text-foreground hover:bg-muted rounded-md border p-1.5 transition-colors'
            title={t('Copy')}
          >
            <Copy className='size-3.5' />
          </button>
        </div>
      </div>

      {/* Description */}
      <p className='text-muted-foreground mt-2 line-clamp-1 flex-1 text-[13px] leading-relaxed sm:mt-4 sm:line-clamp-2 sm:min-h-[2.5rem]'>
        {props.model.description || t('No description available.')}
      </p>

      {/* Footer: left metadata and right performance summary share row alignment */}
      <div className='mt-2 grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-2 gap-y-1 sm:mt-4'>
        <div className='flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1'>
          {primaryGroup && (
            <span className='text-muted-foreground text-xs font-medium'>
              {primaryGroup} {t('Groups')}
            </span>
          )}
          <span className='text-muted-foreground text-xs font-medium'>
            {isTokenBased ? t('Token-based') : t('Per Request')}
          </span>
          {isDynamicPricing && (
            <StatusBadge
              label={
                perSecondFormatted ? t('Per-second') : t('Dynamic Pricing')
              }
              variant='warning'
              copyable={false}
              size='sm'
            />
          )}
        </div>
        <ModelPerfBadge perf={props.perf} className='row-span-2 self-start' />

        <div className='flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-0.5 sm:gap-x-3 sm:gap-y-1'>
          {bottomTags.map((item) => (
            <span key={item} className='text-muted-foreground/70 text-xs'>
              {item}
            </span>
          ))}
          <span className='text-muted-foreground/50 text-xs'>
            {tokenUnitLabel}
          </span>
          {hiddenCount > 0 && (
            <span className='text-muted-foreground/40 text-xs'>
              +{hiddenCount}
            </span>
          )}
        </div>
      </div>
    </div>
  )
})
