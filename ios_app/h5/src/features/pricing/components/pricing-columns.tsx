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
import { type ColumnDef } from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { getLobeIcon } from '@/lib/lobe-icon'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { DataTableColumnHeader } from '@/components/data-table/column-header'
import { GroupBadge } from '@/components/group-badge'
import { DEFAULT_TOKEN_UNIT, QUOTA_TYPE_VALUES } from '../constants'
import {
  formatDynamicFixedRequestPrice,
  formatDynamicUnitPrice,
  getDynamicDisplayGroupRatio,
  getDynamicPricingSummary,
} from '../lib/dynamic-price'
import { parseTags } from '../lib/filters'
import { getGroupRatioBounds, isTokenBasedModel } from '../lib/model-helpers'
import {
  formatFixedPrice,
  formatGroupPrice,
  formatPrice,
  formatRequestPrice,
  stripTrailingZeros,
} from '../lib/price'
import type { PricingModel, TokenUnit } from '../types'

// ----------------------------------------------------------------------------
// Pricing Table Columns
// ----------------------------------------------------------------------------

export interface PricingColumnsOptions {
  tokenUnit?: TokenUnit
  priceRate?: number
  usdExchangeRate?: number
  showRechargePrice?: boolean
  focusedGroup?: string
}

function renderLimitedTags(
  items: string[],
  maxDisplay: number = 3
): React.ReactNode {
  if (items.length === 0)
    return <span className='text-muted-foreground/50 text-xs'>—</span>

  const displayed = items.slice(0, maxDisplay)
  const remaining = items.length - maxDisplay

  return (
    <span className='text-muted-foreground text-xs'>
      {displayed.join(', ')}
      {remaining > 0 && (
        <span className='text-muted-foreground/50'> +{remaining}</span>
      )}
    </span>
  )
}

function renderLimitedGroupBadges(
  groups: string[],
  maxDisplay: number = 2,
  preferredGroup?: string
): React.ReactNode {
  if (groups.length === 0)
    return <span className='text-muted-foreground/50 text-xs'>—</span>

  const orderedGroups =
    preferredGroup && groups.includes(preferredGroup)
      ? [preferredGroup, ...groups.filter((group) => group !== preferredGroup)]
      : groups
  const displayed = orderedGroups.slice(0, maxDisplay)
  const remaining = groups.length - maxDisplay

  return (
    <div className='flex max-w-full items-center gap-1 overflow-hidden'>
      {displayed.map((group) => (
        <GroupBadge key={group} group={group} size='sm' />
      ))}
      {remaining > 0 && (
        <span className='text-muted-foreground/50 text-xs'>+{remaining}</span>
      )}
    </div>
  )
}

function formatDisplayRange(minValue: string, maxValue: string): string {
  const start = stripTrailingZeros(minValue)
  const end = stripTrailingZeros(maxValue)
  return start === end ? start : `${start} - ${end}`
}

export function usePricingColumns(
  options: PricingColumnsOptions = {}
): ColumnDef<PricingModel>[] {
  const { t } = useTranslation()
  const {
    tokenUnit = DEFAULT_TOKEN_UNIT,
    priceRate = 1,
    usdExchangeRate = 1,
    showRechargePrice = false,
    focusedGroup,
  } = options

  const tokenUnitLabel = tokenUnit === 'K' ? '1K' : '1M'

  return [
    // Model column
    {
      accessorKey: 'model_name',
      meta: { label: t('Model') },
      header: ({ column }) => (
        <DataTableColumnHeader column={column} title={t('Model')} />
      ),
      cell: ({ row }) => {
        const model = row.original
        const vendorIcon = model.vendor_icon
          ? getLobeIcon(model.vendor_icon, 14)
          : null

        return (
          <div className='flex min-w-[200px] items-center gap-2'>
            {vendorIcon}
            <span className='truncate font-mono text-sm font-medium'>
              {model.model_name}
            </span>
          </div>
        )
      },
      minSize: 200,
    },

    // Type column
    {
      accessorKey: 'quota_type',
      meta: { label: t('Type') },
      header: t('Type'),
      cell: ({ row }) => {
        const isTokenBased = row.original.quota_type === QUOTA_TYPE_VALUES.TOKEN
        return (
          <span className='text-muted-foreground text-xs font-medium tracking-wider uppercase'>
            {isTokenBased ? t('Token') : t('Request')}
          </span>
        )
      },
      size: 80,
      enableSorting: false,
    },

    // Price column
    {
      accessorKey: 'price',
      meta: { label: t('Price') },
      header: ({ column }) => (
        <DataTableColumnHeader column={column} title={t('Price')} />
      ),
      cell: ({ row }) => {
        const model = row.original
        const displayGroup =
          focusedGroup && model.enable_groups?.includes(focusedGroup)
            ? focusedGroup
            : undefined
        const displayGroupRatio =
          displayGroup !== undefined ? model.group_ratio?.[displayGroup] ?? 1 : 1
        const groupRatioBounds = getGroupRatioBounds(model)
        const hasGroupPriceVariance =
          displayGroup === undefined && groupRatioBounds.hasMultiple
        const dynamicSummary = getDynamicPricingSummary(model, {
          tokenUnit,
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          groupRatioMultiplier:
            displayGroup !== undefined
              ? displayGroupRatio
              : getDynamicDisplayGroupRatio(model),
        })
        const displayGroupRatioMap = {
          __min: groupRatioBounds.min,
          __max: groupRatioBounds.max,
        }

        if (dynamicSummary) {
          if (dynamicSummary.isSpecialExpression) {
            return (
              <div className='max-w-[320px] min-w-[200px]'>
                <div className='text-xs font-medium text-amber-700 dark:text-amber-300'>
                  {t('Special billing expression')}
                </div>
                <div className='text-muted-foreground text-[11px]'>
                  {t('Unable to parse structured pricing')}
                </div>
                <code className='text-muted-foreground/70 mt-1 line-clamp-2 block font-mono text-[10px] leading-relaxed break-all'>
                  {dynamicSummary.rawExpression}
                </code>
              </div>
            )
          }

          if (dynamicSummary.isFixedRequestPricing) {
            const fixedEntries = dynamicSummary.fixedRequestEntries.slice(0, 3)
            return (
              <div className='min-w-[180px]'>
                <span className='font-mono text-sm tabular-nums'>
                  {fixedEntries.map((entry, index) => (
                    <span key={`${entry.label}-${entry.value}`}>
                      {index > 0 && (
                        <span className='text-muted-foreground/40 mx-1'>
                          /
                        </span>
                      )}
                      {hasGroupPriceVariance
                        ? formatDisplayRange(
                            formatDynamicFixedRequestPrice(entry.value, {
                              showRechargePrice,
                              priceRate,
                              usdExchangeRate,
                              groupRatioMultiplier: groupRatioBounds.min,
                            }),
                            formatDynamicFixedRequestPrice(entry.value, {
                              showRechargePrice,
                              priceRate,
                              usdExchangeRate,
                              groupRatioMultiplier: groupRatioBounds.max,
                            })
                          )
                        : stripTrailingZeros(entry.formatted)}
                    </span>
                  ))}
                </span>
                <div className='text-muted-foreground/50 text-[10px]'>
                  / {t('request')}
                  {dynamicSummary.tierCount > 1 &&
                    ` 路 ${t('{{count}} tiers', {
                      count: dynamicSummary.tierCount,
                    })}`}
                </div>
              </div>
            )
          }

          const primaryEntries = dynamicSummary.primaryEntries.slice(0, 2)
          if (primaryEntries.length === 0) {
            return (
              <span className='text-muted-foreground text-xs'>
                {t('Dynamic Pricing')}
              </span>
            )
          }

          return (
            <div className='min-w-[180px]'>
              <span className='font-mono text-sm tabular-nums'>
                {primaryEntries.map((entry, index) => (
                  <span key={entry.key}>
                    {index > 0 && (
                      <span className='text-muted-foreground/40 mx-1'>/</span>
                    )}
                    {hasGroupPriceVariance
                      ? formatDisplayRange(
                          formatDynamicUnitPrice(entry.value, {
                            tokenUnit,
                            showRechargePrice,
                            priceRate,
                            usdExchangeRate,
                            groupRatioMultiplier: groupRatioBounds.min,
                          }),
                          formatDynamicUnitPrice(entry.value, {
                            tokenUnit,
                            showRechargePrice,
                            priceRate,
                            usdExchangeRate,
                            groupRatioMultiplier: groupRatioBounds.max,
                          })
                        )
                      : stripTrailingZeros(entry.formatted)}
                  </span>
                ))}
              </span>
              <div className='text-muted-foreground/50 text-[10px]'>
                / {tokenUnitLabel} tokens
                {dynamicSummary.tierCount > 1 &&
                  ` · ${t('{{count}} tiers', {
                    count: dynamicSummary.tierCount,
                  })}`}
              </div>
            </div>
          )
        }

        const isTokenBased = isTokenBasedModel(model)

        if (isTokenBased) {
          const formatTokenPriceForTable = (
            type: 'input' | 'output' | 'cache'
          ) => {
            if (displayGroup !== undefined) {
              return stripTrailingZeros(
                formatGroupPrice(
                  model,
                  displayGroup,
                  type,
                  tokenUnit,
                  showRechargePrice,
                  priceRate,
                  usdExchangeRate,
                  model.group_ratio || {}
                )
              )
            }

            if (hasGroupPriceVariance) {
              return formatDisplayRange(
                formatGroupPrice(
                  model,
                  '__min',
                  type,
                  tokenUnit,
                  showRechargePrice,
                  priceRate,
                  usdExchangeRate,
                  displayGroupRatioMap
                ),
                formatGroupPrice(
                  model,
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

            return stripTrailingZeros(
              formatPrice(
                model,
                type,
                tokenUnit,
                showRechargePrice,
                priceRate,
                usdExchangeRate
              )
            )
          }
          const inputPrice = formatTokenPriceForTable('input')
          const outputPrice = formatTokenPriceForTable('output')

          return (
            <div className='min-w-[160px]'>
              <span className='font-mono text-sm tabular-nums'>
                {inputPrice}
                <span className='text-muted-foreground/40 mx-1'>/</span>
                {outputPrice}
              </span>
              <div className='text-muted-foreground/50 text-[10px]'>
                / {tokenUnitLabel} tokens
              </div>
            </div>
          )
        }

        const price =
          displayGroup !== undefined
            ? stripTrailingZeros(
                formatFixedPrice(
                  model,
                  displayGroup,
                  showRechargePrice,
                  priceRate,
                  usdExchangeRate,
                  model.group_ratio || {}
                )
              )
            : hasGroupPriceVariance
              ? formatDisplayRange(
                  formatFixedPrice(
                    model,
                    '__min',
                    showRechargePrice,
                    priceRate,
                    usdExchangeRate,
                    displayGroupRatioMap
                  ),
                  formatFixedPrice(
                    model,
                    '__max',
                    showRechargePrice,
                    priceRate,
                    usdExchangeRate,
                    displayGroupRatioMap
                  )
                )
              : stripTrailingZeros(
                  formatRequestPrice(
                    model,
                    showRechargePrice,
                    priceRate,
                    usdExchangeRate
                  )
                )

        return (
          <div className='min-w-[100px]'>
            <span className='font-mono text-sm tabular-nums'>{price}</span>
            <div className='text-muted-foreground/50 text-[10px]'>
              / {t('request')}
            </div>
          </div>
        )
      },
      size: 180,
      enableSorting: false,
    },

    // Cached price column (Vercel AI Gateway style)
    {
      id: 'cached_price',
      meta: { label: t('Cached') },
      header: t('Cached'),
      cell: ({ row }) => {
        const model = row.original
        const displayGroup =
          focusedGroup && model.enable_groups?.includes(focusedGroup)
            ? focusedGroup
            : undefined
        const dynamicSummary = getDynamicPricingSummary(model, {
          tokenUnit,
          showRechargePrice,
          priceRate,
          usdExchangeRate,
          groupRatioMultiplier:
            displayGroup !== undefined
              ? model.group_ratio?.[displayGroup] ?? 1
              : getDynamicDisplayGroupRatio(model),
        })

        if (dynamicSummary) {
          if (dynamicSummary.isSpecialExpression) {
            return (
              <span className='text-muted-foreground/50 text-xs'>
                {t('Special billing expression')}
              </span>
            )
          }

          const cacheEntry = dynamicSummary.entries.find(
            (entry) => entry.field === 'cacheReadPrice'
          )
          if (!cacheEntry) {
            return <span className='text-muted-foreground/30 text-xs'>—</span>
          }

          return (
            <div className='min-w-[80px]'>
              <span className='font-mono text-sm tabular-nums'>
                {stripTrailingZeros(cacheEntry.formatted)}
              </span>
              <div className='text-muted-foreground/50 text-[10px]'>
                / {tokenUnitLabel}
              </div>
            </div>
          )
        }

        const isTokenBased = isTokenBasedModel(model)

        if (!isTokenBased || model.cache_ratio == null) {
          return <span className='text-muted-foreground/30 text-xs'>—</span>
        }

        const cachedPrice = stripTrailingZeros(
          displayGroup !== undefined
            ? formatGroupPrice(
                model,
                displayGroup,
                'cache',
                tokenUnit,
                showRechargePrice,
                priceRate,
                usdExchangeRate,
                model.group_ratio || {}
              )
            : formatPrice(
                model,
                'cache',
                tokenUnit,
                showRechargePrice,
                priceRate,
                usdExchangeRate
              )
        )

        return (
          <div className='min-w-[80px]'>
            <span className='font-mono text-sm tabular-nums'>
              {cachedPrice}
            </span>
            <div className='text-muted-foreground/50 text-[10px]'>
              / {tokenUnitLabel}
            </div>
          </div>
        )
      },
      size: 110,
      enableSorting: false,
    },

    // Vendor column
    {
      accessorKey: 'vendor_name',
      meta: { label: t('Vendor') },
      header: t('Vendor'),
      cell: ({ row }) => {
        const model = row.original
        if (!model.vendor_name) {
          return <span className='text-muted-foreground/50 text-xs'>—</span>
        }
        const vendorIcon = model.vendor_icon
          ? getLobeIcon(model.vendor_icon, 12)
          : null
        return (
          <span className='text-muted-foreground flex items-center gap-1.5 text-xs'>
            {vendorIcon}
            {model.vendor_name}
          </span>
        )
      },
      size: 130,
      enableSorting: false,
    },

    // Tags column
    {
      accessorKey: 'tags',
      meta: { label: t('Tags') },
      header: t('Tags'),
      cell: ({ row }) => {
        const tags = parseTags(row.original.tags)
        if (tags.length === 0) {
          return <span className='text-muted-foreground/50 text-xs'>—</span>
        }

        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger render={<div />}>
                {renderLimitedTags(tags, 2)}
              </TooltipTrigger>
              {tags.length > 2 && (
                <TooltipContent side='top' className='max-w-[280px] p-2'>
                  <span className='text-xs'>{tags.join(', ')}</span>
                </TooltipContent>
              )}
            </Tooltip>
          </TooltipProvider>
        )
      },
      size: 140,
      enableSorting: false,
    },

    // Endpoints column
    {
      accessorKey: 'supported_endpoint_types',
      meta: { label: t('Endpoints') },
      header: t('Endpoints'),
      cell: ({ row }) => {
        const endpoints = row.original.supported_endpoint_types || []
        if (endpoints.length === 0) {
          return <span className='text-muted-foreground/50 text-xs'>—</span>
        }

        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger render={<div />}>
                {renderLimitedTags(endpoints, 2)}
              </TooltipTrigger>
              {endpoints.length > 2 && (
                <TooltipContent side='top' className='max-w-[280px] p-2'>
                  <span className='text-xs'>{endpoints.join(', ')}</span>
                </TooltipContent>
              )}
            </Tooltip>
          </TooltipProvider>
        )
      },
      size: 130,
      enableSorting: false,
    },

    // Enable Groups column
    {
      accessorKey: 'enable_groups',
      meta: { label: t('Groups') },
      header: t('Groups'),
      cell: ({ row }) => {
        const groups = row.original.enable_groups || []
        if (groups.length === 0) {
          return <span className='text-muted-foreground/50 text-xs'>—</span>
        }

        return (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger render={<div />}>
                {renderLimitedGroupBadges(groups, 2, focusedGroup)}
              </TooltipTrigger>
              {groups.length > 2 && (
                <TooltipContent side='top' className='max-w-[280px] p-2'>
                  <div className='flex flex-wrap gap-1'>
                    {groups.map((group) => (
                      <GroupBadge key={group} group={group} size='sm' />
                    ))}
                  </div>
                </TooltipContent>
              )}
            </Tooltip>
          </TooltipProvider>
        )
      },
      size: 130,
      enableSorting: false,
    },
  ]
}
