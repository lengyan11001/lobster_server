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
import { KeyRound, Network, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  API_KEYS_TAB_VALUES,
  type ApiKeysTabValue,
} from './keys-page-tabs.constants'

export function KeysPageTabs(props: {
  value: ApiKeysTabValue
  onValueChange: (value: ApiKeysTabValue) => void
}) {
  const { t } = useTranslation()

  return (
    <Tabs
      value={props.value}
      onValueChange={(value) => props.onValueChange(value as ApiKeysTabValue)}
      className='gap-0'
    >
      <TabsList className='bg-muted/50 border-border/60 h-auto w-full justify-start gap-1 overflow-x-auto rounded-2xl border p-1.5'>
        <TabsTrigger
          value={API_KEYS_TAB_VALUES.apiKeys}
          className='h-10 gap-2 rounded-xl px-3.5 text-sm'
        >
          <KeyRound className='size-4' />
          {t('API Keys')}
        </TabsTrigger>
        <TabsTrigger
          value={API_KEYS_TAB_VALUES.platformAccess}
          className='h-10 gap-2 rounded-xl px-3.5 text-sm'
        >
          <Network className='size-4' />
          {t('Current platform access address')}
        </TabsTrigger>
        <TabsTrigger
          value={API_KEYS_TAB_VALUES.codexTools}
          className='h-10 gap-2 rounded-xl px-3.5 text-sm'
        >
          <Sparkles className='size-4' />
          {t('Codex / Claude Code free tool')}
        </TabsTrigger>
      </TabsList>
    </Tabs>
  )
}
