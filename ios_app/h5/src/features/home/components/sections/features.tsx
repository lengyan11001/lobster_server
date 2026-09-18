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
import { Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimateInView } from '@/components/animate-in-view'

interface FeaturesProps {
  className?: string
}

const scenarios = [
  {
    num: '01',
    title: 'Bulk Content Production',
    desc: 'Generate video scripts, covers, ad creatives, and social posts with text, image, and video models.',
  },
  {
    num: '02',
    title: 'E-commerce Visual Marketing',
    desc: 'Create product scene images, try-on assets, and campaign posters while reducing shooting and outsourcing costs.',
  },
  {
    num: '03',
    title: 'Intelligent Support Upgrade',
    desc: 'Route requests to the right model by complexity and balance experience, cost, and stability.',
  },
  {
    num: '04',
    title: 'Code-assisted Development',
    desc: 'Unify Claude, GPT, DeepSeek, and other coding models so different technical stacks can match the right assistant.',
  },
  {
    num: '05',
    title: 'Financial Report Generation',
    desc: 'Use long-context models to analyze statements and announcements, then generate structured research summaries and reports.',
  },
  {
    num: '06',
    title: 'Personalized Education Tutoring',
    desc: 'Select models dynamically by student level, covering the full flow from basic Q&A to competition coaching.',
  },
] as const

export function Features(_props: FeaturesProps) {
  const { t } = useTranslation()

  return (
    <section className='business-scenarios-section relative z-10 px-6 py-20 md:py-[6.5rem]'>
      <div className='mx-auto max-w-7xl'>
        <AnimateInView className='mx-auto mb-10 max-w-3xl text-center md:mb-12'>
          <p className='text-muted-foreground mb-3 text-[11px] font-semibold tracking-[0.12em] uppercase'>
            {t('Business Scenarios')}
          </p>
          <h2 className='text-3xl leading-tight font-bold md:text-4xl'>
            {t('From creative production to enterprise automation')}
          </h2>
        </AnimateInView>

        <div className='grid gap-5 md:grid-cols-3 md:gap-6'>
          {scenarios.map((scenario, index) => (
            <AnimateInView
              key={scenario.num}
              delay={index * 80}
              animation='fade-up'
              className='business-scenario-card'
            >
              <div className='business-scenario-icon'>
                <Sparkles className='size-5' strokeWidth={1.8} />
              </div>
              <p className='business-scenario-num'>{scenario.num}</p>
              <h3 className='business-scenario-title'>{t(scenario.title)}</h3>
              <p className='business-scenario-desc'>{t(scenario.desc)}</p>
            </AnimateInView>
          ))}
        </div>
      </div>
    </section>
  )
}
