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
import { formatBillingCurrencyFromUSD } from '@/lib/currency'

const PER_SECOND_EXPR_PATTERN =
  /^tier\("per_second",\s*\(param\("duration"\)\s*!=\s*nil\s*\?\s*param\("duration"\)\s*:\s*\(param\("seconds"\)\s*!=\s*nil\s*\?\s*param\("seconds"\)\s*:\s*\(param\("video_config\.video_length"\)\s*!=\s*nil\s*\?\s*param\("video_config\.video_length"\)\s*:\s*1\)\)\)\s*\*\s*([0-9.eE+-]+)\s*\*\s*1000000\)$/

export function buildPerSecondBillingExpr(pricePerSecond: string): string {
  const price = Number(pricePerSecond)
  const normalizedPrice = Number.isFinite(price)
    ? Number.parseFloat(price.toFixed(12)).toString()
    : '0'
  return `tier("per_second", (param("duration") != nil ? param("duration") : (param("seconds") != nil ? param("seconds") : (param("video_config.video_length") != nil ? param("video_config.video_length") : 1))) * ${normalizedPrice} * 1000000)`
}

export function parsePerSecondBillingExpr(expr: string): string | null {
  const match = (expr || '').trim().match(PER_SECOND_EXPR_PATTERN)
  if (!match) return null
  const price = Number(match[1])
  if (!Number.isFinite(price)) return null
  return Number.parseFloat(price.toFixed(12)).toString()
}

export function isPerSecondBillingExpr(expr: string): boolean {
  return parsePerSecondBillingExpr(expr) !== null
}

export function formatPerSecondPrice(
  pricePerSecond: number,
  options: {
    showRechargePrice?: boolean
    priceRate?: number
    usdExchangeRate?: number
    groupRatioMultiplier?: number
  } = {}
): string {
  const groupRatio = options.groupRatioMultiplier ?? 1
  const priceRate = options.priceRate ?? 1
  const usdExchangeRate = options.usdExchangeRate ?? 1
  let priceUSD = pricePerSecond * groupRatio
  if (options.showRechargePrice) {
    priceUSD = (priceUSD * priceRate) / usdExchangeRate
  }

  return formatBillingCurrencyFromUSD(priceUSD, {
    digitsLarge: 4,
    digitsSmall: 6,
    abbreviate: false,
  })
}
