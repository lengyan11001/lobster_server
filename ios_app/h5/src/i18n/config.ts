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
import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'
import fr from './locales/fr.json'
import ja from './locales/ja.json'
import ru from './locales/ru.json'
import vi from './locales/vi.json'
import zh from './locales/zh.json'

function rebrandText(value: string): string {
  return value
    .replace(/Xenlion API/g, 'OpenMind API')
    .replace(/New API/g, 'OpenMind API')
    .replace(/NewAPI/g, 'OpenMind API')
    .replace(/new-api-key-tool/gi, 'openmind-key-tool')
    .replace(/xenlion-api/gi, 'openmind-api')
    .replace(/new-api/gi, 'openmind-api')
}

export const resources = {
  en,
  zh,
  fr,
  ru,
  ja,
  vi,
} as const

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    supportedLngs: ['en', 'zh', 'fr', 'ru', 'ja', 'vi'],
    load: 'languageOnly', // Convert zh-CN -> zh
    nsSeparator: false, // Allow literal colons in keys (e.g., URLs, labels)
    debug: import.meta.env.DEV,
    interpolation: {
      escapeValue: false, // not needed for react as it escapes by default
    },
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
    },
  })

const rawT = i18n.t.bind(i18n)
i18n.t = ((...args: Parameters<typeof rawT>) => {
  const result = rawT(...args)
  if (typeof result === 'string') {
    return rebrandText(result)
  }
  return result
}) as typeof i18n.t

export default i18n
