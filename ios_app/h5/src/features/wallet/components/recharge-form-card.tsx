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
  ArrowRight,
  AlertTriangle,
  CalendarDays,
  ClipboardCheck,
  ExternalLink,
  Gift,
  Loader2,
  Receipt,
  ShoppingBag,
  Ticket,
  WalletCards,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { TitledCard } from '@/components/ui/titled-card'
import type {
  CreemProduct,
  PaymentMethod,
  PresetAmount,
  TopupInfo,
  WaffoPayMethod,
} from '../types'
import { CreemProductsSection } from './creem-products-section'

const DEFAULT_GIFT_CARD_SHOP_URL = 'https://pay.ldxp.cn/shop/H9AH3CFY'

const METERED_GIFT_CARD_PRODUCTS = [
  {
    key: 'metered-5',
    nameKey: '5 CNY Recharge',
    descriptionKey: 'Pay-as-you-go balance for platform model usage',
    price: '¥5',
    quota: '$5',
    badgeKey: 'In Stock',
  },
  {
    key: 'metered-10',
    nameKey: '10 CNY Recharge',
    descriptionKey: 'Pay-as-you-go balance for platform model usage',
    price: '¥10',
    quota: '$11',
    badgeKey: 'In Stock',
  },
  {
    key: 'metered-20',
    nameKey: '20 CNY Recharge',
    descriptionKey: 'Pay-as-you-go balance for platform model usage',
    price: '¥20',
    quota: '$20',
    badgeKey: 'Stock Normal',
  },
  {
    key: 'metered-50',
    nameKey: '50 CNY Recharge',
    descriptionKey: 'Pay-as-you-go balance for platform model usage',
    price: '¥50',
    quota: '$50',
    badgeKey: 'In Stock',
  },
] as const

const TRIAL_GIFT_CARD_PRODUCTS = [
  {
    key: 'daily',
    nameKey: '10 CNY Trial Daily Card',
    descriptionKey: 'Short-term access for trying platform models',
    price: '¥5.9',
    quota: '$10',
    validityKey: 'Valid for 1 day',
    badgeKey: 'Stock Normal',
  },
  {
    key: 'weekly',
    nameKey: '50 CNY Trial Weekly Card',
    descriptionKey: 'Suitable for short-term stable usage',
    price: '¥40',
    quota: '$50',
    validityKey: 'Valid for 7 days',
    badgeKey: 'In Stock',
  },
  {
    key: 'monthly',
    nameKey: '200 CNY Trial Monthly Card',
    descriptionKey: 'Suitable for long-term stable usage',
    price: '¥140',
    quota: '$200',
    validityKey: 'Valid for 1 month',
    badgeKey: 'In Stock',
  },
] as const

interface RechargeFormCardProps {
  topupInfo: TopupInfo | null
  presetAmounts: PresetAmount[]
  selectedPreset: number | null
  onSelectPreset: (preset: PresetAmount) => void
  topupAmount: number
  onTopupAmountChange: (amount: number) => void
  paymentAmount: number
  calculating: boolean
  onPaymentMethodSelect: (method: PaymentMethod) => void
  paymentLoading: string | null
  redemptionCode: string
  onRedemptionCodeChange: (code: string) => void
  onRedeem: () => void
  redeeming: boolean
  topupLink?: string
  loading?: boolean
  priceRatio?: number
  usdExchangeRate?: number
  onOpenBilling?: () => void
  creemProducts?: CreemProduct[]
  enableCreemTopup?: boolean
  onCreemProductSelect?: (product: CreemProduct) => void
  enableWaffoTopup?: boolean
  waffoPayMethods?: WaffoPayMethod[]
  waffoMinTopup?: number
  onWaffoMethodSelect?: (method: WaffoPayMethod, index: number) => void
  enableWaffoPancakeTopup?: boolean
}

export function RechargeFormCard({
  selectedPreset,
  onSelectPreset,
  redemptionCode,
  onRedemptionCodeChange,
  onRedeem,
  redeeming,
  topupLink,
  loading,
  onOpenBilling,
  creemProducts,
  enableCreemTopup,
  onCreemProductSelect,
}: RechargeFormCardProps) {
  const { t } = useTranslation()
  const giftCardUrl = topupLink || DEFAULT_GIFT_CARD_SHOP_URL

  const openGiftCardShop = () => {
    window.open(giftCardUrl, '_blank', 'noopener,noreferrer')
  }

  if (loading) {
    return (
      <Card className='gap-0 overflow-hidden py-0'>
        <CardHeader className='border-b p-3 !pb-3 sm:p-5 sm:!pb-5'>
          <Skeleton className='h-6 w-32' />
          <Skeleton className='mt-2 h-4 w-48' />
        </CardHeader>
        <CardContent className='space-y-4 p-3 sm:space-y-6 sm:p-5'>
          <div className='space-y-4 sm:space-y-6'>
            <div className='space-y-3'>
              <Skeleton className='h-3 w-28' />
              <div className='grid grid-cols-2 gap-3 sm:grid-cols-4'>
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className='h-[82px] rounded-lg' />
                ))}
              </div>
            </div>
            <Skeleton className='h-[112px] rounded-lg' />
          </div>

          <div className='space-y-3 border-t pt-8'>
            <Skeleton className='h-3 w-28' />
            <div className='flex gap-2'>
              <Skeleton className='h-10 flex-1' />
              <Skeleton className='h-10 w-20' />
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  const renderMeteredProduct = (
    product: (typeof METERED_GIFT_CARD_PRODUCTS)[number],
    index: number
  ) => {
    const preset = { value: index + 1 }
    return (
      <Button
        key={product.key}
        variant='outline'
        className={cn(
          'hover:border-foreground flex min-h-[132px] flex-col items-start justify-between rounded-lg px-3 py-3 text-left whitespace-normal sm:min-h-[144px] sm:p-4',
          selectedPreset === preset.value
            ? 'border-foreground bg-foreground/5'
            : 'border-muted'
        )}
        onClick={() => {
          onSelectPreset(preset)
          openGiftCardShop()
        }}
      >
        <div className='flex w-full items-center justify-between gap-2'>
          <div className='flex min-w-0 items-center gap-1.5'>
            <Gift className='text-muted-foreground h-4 w-4 shrink-0' />
            <span className='truncate text-xs font-medium'>
              {t(product.nameKey)}
            </span>
          </div>
          <span className='bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-medium'>
            {t(product.badgeKey)}
          </span>
        </div>
        <div className='w-full space-y-2'>
          <div className='text-muted-foreground text-xs leading-5'>
            {t(product.descriptionKey)}
          </div>
          <div className='flex items-end gap-2'>
            <span className='text-2xl font-bold tracking-tight'>
              {product.price}
            </span>
            <span className='text-muted-foreground pb-0.5 text-xs'>
              {t('Includes {{quota}} balance', {
                quota: product.quota,
              })}
            </span>
          </div>
          <div className='text-foreground flex items-center gap-1.5 text-xs font-medium'>
            <span>{t('Buy on gift card page')}</span>
            <ArrowRight className='h-3.5 w-3.5' />
          </div>
        </div>
      </Button>
    )
  }

  const renderTrialProduct = (
    product: (typeof TRIAL_GIFT_CARD_PRODUCTS)[number],
    index: number
  ) => {
    const preset = { value: index + 101 }
    return (
      <Button
        key={product.key}
        variant='outline'
        className={cn(
          'hover:border-foreground flex min-h-[156px] flex-col items-start justify-between rounded-lg px-3 py-3 text-left whitespace-normal sm:min-h-[168px] sm:p-4',
          selectedPreset === preset.value
            ? 'border-foreground bg-foreground/5'
            : 'border-muted'
        )}
        onClick={() => {
          onSelectPreset(preset)
          openGiftCardShop()
        }}
      >
        <div className='flex w-full items-center justify-between gap-2'>
          <div className='flex min-w-0 items-center gap-1.5'>
            <Gift className='text-muted-foreground h-4 w-4 shrink-0' />
            <span className='truncate text-xs font-medium'>
              {t(product.nameKey)}
            </span>
          </div>
          <span className='bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-medium'>
            {t(product.badgeKey)}
          </span>
        </div>
        <div className='w-full space-y-2'>
          <div>
            <div className='text-muted-foreground text-xs leading-5'>
              {t(product.descriptionKey)}
            </div>
            <div className='mt-2 flex items-end gap-2'>
              <span className='text-2xl font-bold tracking-tight'>
                {product.price}
              </span>
              <span className='text-muted-foreground pb-0.5 text-xs'>
                {t('Includes {{quota}} balance', {
                  quota: product.quota,
                })}
              </span>
            </div>
          </div>
          <div className='text-muted-foreground flex items-center gap-1.5 text-xs'>
            <CalendarDays className='h-3.5 w-3.5' />
            <span>{t(product.validityKey)}</span>
          </div>
          <div className='text-foreground flex items-center gap-1.5 text-xs font-medium'>
            <span>{t('Buy on gift card page')}</span>
            <ArrowRight className='h-3.5 w-3.5' />
          </div>
        </div>
      </Button>
    )
  }

  return (
    <TitledCard
      title={t('Add Funds')}
      description={t('Buy a gift card, then redeem the code here')}
      icon={<WalletCards className='h-4 w-4' />}
      action={
        onOpenBilling ? (
          <Button
            variant='outline'
            size='sm'
            onClick={onOpenBilling}
            className='w-full gap-2 sm:w-auto'
          >
            <Receipt className='h-4 w-4' />
            {t('Order History')}
          </Button>
        ) : null
      }
      contentClassName='space-y-4 sm:space-y-6'
    >
      <div className='rounded-lg border border-rose-200 bg-rose-50 p-4 text-rose-950 sm:p-5 dark:border-rose-900/60 dark:bg-rose-950/20 dark:text-rose-100'>
        <div className='flex items-start gap-3'>
          <AlertTriangle className='mt-0.5 h-5 w-5 shrink-0 text-rose-600 dark:text-rose-300' />
          <div className='min-w-0 flex-1 space-y-3'>
            <p className='text-sm leading-6 font-semibold sm:text-base'>
              {t('Mainland China recharge warning')}
            </p>
            <p className='text-muted-foreground text-sm leading-6 dark:text-rose-100/80'>
              {t('Run this command to check your current network environment:')}
            </p>
            <div className='bg-muted/70 text-foreground overflow-x-auto rounded-md px-3 py-2 font-mono text-sm'>
              npx -y easyrouter-netcheck
            </div>
          </div>
        </div>
      </div>

      <div className='space-y-4 sm:space-y-5'>
        <div className='bg-muted/20 rounded-lg border p-3 sm:p-4'>
          <div className='grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center'>
            <div className='space-y-1'>
              <div className='flex items-center gap-2'>
                <ShoppingBag className='text-muted-foreground h-4 w-4' />
                <h3 className='text-sm font-semibold'>
                  {t('Purchase gift cards')}
                </h3>
              </div>
              <p className='text-muted-foreground text-sm'>
                {t(
                  'Gift cards are universal platform balance. They can be used with all supported models and are not limited to Codex or Claude.'
                )}
              </p>
            </div>
            <Button onClick={openGiftCardShop} className='gap-2'>
              {t('Buy Gift Card')}
              <ExternalLink className='h-4 w-4' />
            </Button>
          </div>
        </div>

        <div className='grid gap-2.5 sm:grid-cols-3'>
          {[
            {
              icon: ShoppingBag,
              title: t('Choose a gift card'),
              text: t(
                'Open the gift card page and choose pay-as-you-go recharge first, or trial cards below.'
              ),
            },
            {
              icon: ClipboardCheck,
              title: t('Copy the code'),
              text: t(
                'After payment succeeds, copy the redemption code from the order page.'
              ),
            },
            {
              icon: Ticket,
              title: t('Redeem balance'),
              text: t(
                'Paste the code below. The redeemed balance is shared by all platform models.'
              ),
            },
          ].map((step, index) => (
            <div
              key={step.title}
              className='bg-background rounded-lg border p-3 sm:p-4'
            >
              <div className='flex items-center gap-2'>
                <div className='bg-muted text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-md text-xs font-semibold'>
                  {index + 1}
                </div>
                <step.icon className='text-muted-foreground h-4 w-4 shrink-0' />
                <div className='truncate text-sm font-medium'>{step.title}</div>
              </div>
              <p className='text-muted-foreground mt-2 text-xs leading-5'>
                {step.text}
              </p>
            </div>
          ))}
        </div>

        <div className='space-y-5'>
          <div className='space-y-2.5 sm:space-y-3'>
            <Label className='text-muted-foreground text-xs font-medium tracking-wider uppercase'>
              {t('Pay-as-you-go products')}
            </Label>
            <div className='grid grid-cols-2 gap-1.5 sm:gap-3 md:grid-cols-4'>
              {METERED_GIFT_CARD_PRODUCTS.map(renderMeteredProduct)}
            </div>
          </div>

          <div className='space-y-2.5 sm:space-y-3'>
            <Label className='text-muted-foreground text-xs font-medium tracking-wider uppercase'>
              {t('Trial card products')}
            </Label>
            <div className='grid grid-cols-1 gap-1.5 sm:grid-cols-3 sm:gap-3'>
              {TRIAL_GIFT_CARD_PRODUCTS.map(renderTrialProduct)}
            </div>
          </div>

          <p className='text-muted-foreground text-xs leading-5'>
            {t(
              'All gift card balance is universal across the platform. It can be used for text, image, video, Codex, Claude, and other available models according to their billing rules.'
            )}
          </p>
        </div>
      </div>

      {enableCreemTopup &&
        Array.isArray(creemProducts) &&
        creemProducts.length > 0 &&
        onCreemProductSelect && (
          <div className='space-y-2.5 border-t pt-4 sm:space-y-3 sm:pt-6'>
            <Label className='text-muted-foreground text-xs font-medium tracking-wider uppercase'>
              {t('Creem Payment')}
            </Label>
            <CreemProductsSection
              products={creemProducts}
              onProductSelect={onCreemProductSelect}
            />
          </div>
        )}

      <div className='space-y-2.5 border-t pt-4 sm:space-y-3 sm:pt-6'>
        <div className='grid gap-1'>
          <div className='flex items-center gap-2'>
            <Ticket className='text-muted-foreground h-4 w-4' />
            <Label
              htmlFor='redemption-code'
              className='text-muted-foreground text-xs font-medium tracking-wider uppercase'
            >
              {t('Redeem gift card code')}
            </Label>
          </div>
          <p className='text-muted-foreground text-xs leading-5'>
            {t(
              'Paste the redemption code you received after purchasing the gift card. The balance will be added immediately after a successful redemption and can be used across supported platform models.'
            )}
          </p>
        </div>
        <div className='grid grid-cols-[minmax(0,1fr)_auto] gap-2'>
          <Input
            id='redemption-code'
            value={redemptionCode}
            onChange={(e) => onRedemptionCodeChange(e.target.value)}
            placeholder={t('Paste redemption code here')}
            className='h-9 min-w-0'
          />
          <Button
            onClick={onRedeem}
            disabled={redeeming}
            variant='outline'
            className='h-9 px-4'
          >
            {redeeming && <Loader2 className='mr-2 h-4 w-4 animate-spin' />}
            {t('Redeem')}
          </Button>
        </div>
        <p className='text-muted-foreground text-xs'>
          {t('No code yet?')}{' '}
          <a
            href={giftCardUrl}
            target='_blank'
            rel='noopener noreferrer'
            className='inline-flex items-center gap-1 underline-offset-4 hover:underline'
          >
            {t('Buy a gift card first')}
            <ExternalLink className='h-3 w-3' />
          </a>
        </p>
      </div>
    </TitledCard>
  )
}
