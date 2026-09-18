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
import { useNavigate, useSearch } from '@tanstack/react-router'
import { KeyRound, Network, Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { SectionPageLayout } from '@/components/layout'
import { ApiKeysDialogs } from './components/api-keys-dialogs'
import {
  CodexToolsPanel,
  PlatformAccessPanel,
} from './components/api-keys-hub-panels'
import { ApiKeysPrimaryButtons } from './components/api-keys-primary-buttons'
import { ApiKeysProvider } from './components/api-keys-provider'
import { ApiKeysTable } from './components/api-keys-table'
import { KeysPageTabs } from './components/keys-page-tabs'
import {
  API_KEYS_TAB_VALUES,
  type ApiKeysTabValue,
} from './components/keys-page-tabs.constants'

type ApiKeysSearch = {
  page?: number
  pageSize?: number
  status?: string[]
  filter?: string
  tab?: ApiKeysTabValue
}

export function ApiKeys() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const search = useSearch({ from: '/_authenticated/keys/' }) as ApiKeysSearch

  const activeTab = search.tab || API_KEYS_TAB_VALUES.apiKeys

  const handleTabChange = (tab: ApiKeysTabValue) => {
    navigate({
      to: '/keys',
      replace: true,
      search: (prev) => ({
        ...prev,
        tab,
      }),
    })
  }

  const actionButtons =
    activeTab === API_KEYS_TAB_VALUES.apiKeys ? (
      <>
        <ApiKeysPrimaryButtons />
        <Button
          size='sm'
          variant='outline'
          onClick={() => handleTabChange(API_KEYS_TAB_VALUES.platformAccess)}
        >
          <Network className='size-4' />
          {t('Current platform access address')}
        </Button>
        <Button
          size='sm'
          variant='outline'
          onClick={() => handleTabChange(API_KEYS_TAB_VALUES.codexTools)}
        >
          <Sparkles className='size-4' />
          {t('Codex / Claude Code free tool')}
        </Button>
      </>
    ) : (
      <Button
        size='sm'
        variant='outline'
        onClick={() => handleTabChange(API_KEYS_TAB_VALUES.apiKeys)}
      >
        <KeyRound className='size-4' />
        {t('Back to API Keys')}
      </Button>
    )

  const pageTitle =
    activeTab === API_KEYS_TAB_VALUES.platformAccess
      ? t('Current platform access address')
      : activeTab === API_KEYS_TAB_VALUES.codexTools
        ? t('Codex / Claude Code free tool')
        : t('API Keys')

  const pageDescription =
    activeTab === API_KEYS_TAB_VALUES.platformAccess
      ? t(
          'Choose the correct endpoint for GPT, Codex, OpenAI-compatible, and Claude-compatible clients before copying your key.'
        )
      : activeTab === API_KEYS_TAB_VALUES.codexTools
        ? t(
            'Download the helper installer, follow the setup tutorial, and finish local client configuration with the current platform endpoint.'
          )
        : t('Manage your API keys for accessing the service')

  return (
    <ApiKeysProvider>
      <SectionPageLayout>
        <SectionPageLayout.Title>{pageTitle}</SectionPageLayout.Title>
        <SectionPageLayout.Actions>{actionButtons}</SectionPageLayout.Actions>
        <SectionPageLayout.Content>
          <div className='space-y-4'>
            <div className='space-y-3'>
              <KeysPageTabs value={activeTab} onValueChange={handleTabChange} />
              <div className='text-muted-foreground border-border/60 bg-muted/30 rounded-2xl border px-4 py-3 text-sm leading-6'>
                {pageDescription}
              </div>
            </div>

            {activeTab === API_KEYS_TAB_VALUES.apiKeys ? (
              <ApiKeysTable />
            ) : null}

            {activeTab === API_KEYS_TAB_VALUES.platformAccess ? (
              <PlatformAccessPanel />
            ) : null}

            {activeTab === API_KEYS_TAB_VALUES.codexTools ? (
              <CodexToolsPanel
                onGoToAccess={() =>
                  handleTabChange(API_KEYS_TAB_VALUES.platformAccess)
                }
                onGoToKeys={() => handleTabChange(API_KEYS_TAB_VALUES.apiKeys)}
              />
            ) : null}
          </div>
        </SectionPageLayout.Content>
      </SectionPageLayout>

      <ApiKeysDialogs />
    </ApiKeysProvider>
  )
}
