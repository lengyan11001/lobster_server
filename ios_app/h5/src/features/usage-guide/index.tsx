import {
  AlertTriangle,
  CheckCircle2,
  Copy,
  ExternalLink,
  KeyRound,
  Laptop,
  MessageSquare,
  Network,
  Route,
  ShieldCheck,
} from 'lucide-react'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { PublicLayout } from '@/components/layout'
import { cn } from '@/lib/utils'

type Step = {
  title: string
  body: string
  note?: string
}

type GuideSection = {
  id: string
  title: string
  icon: React.ComponentType<{ className?: string }>
  summary: string
  steps: Step[]
}

function getCurrentOrigin() {
  if (typeof window === 'undefined') return 'https://www.openmindapi.com'
  return window.location.origin
}

function CopyButton({ value }: { value: string }) {
  const { t } = useTranslation()

  return (
    <Button
      type='button'
      variant='outline'
      size='icon'
      className='h-8 w-8 shrink-0'
      onClick={() => void navigator.clipboard?.writeText(value)}
      title={t('Copy')}
    >
      <Copy className='h-4 w-4' />
    </Button>
  )
}

function EndpointRow(props: {
  label: string
  value: string
  description: string
  tone?: 'default' | 'warning'
}) {
  return (
    <div className='grid gap-2 border-b py-3 last:border-b-0 sm:grid-cols-[160px_minmax(0,1fr)] sm:items-start'>
      <div className='flex items-center gap-2'>
        <span className='text-sm font-medium'>{props.label}</span>
        {props.tone === 'warning' && (
          <Badge variant='outline' className='text-amber-600'>
            注意
          </Badge>
        )}
      </div>
      <div className='space-y-2'>
        <div className='bg-muted/60 flex items-center gap-2 rounded-md border px-3 py-2'>
          <code className='min-w-0 flex-1 break-all text-sm'>{props.value}</code>
          <CopyButton value={props.value} />
        </div>
        <p className='text-muted-foreground text-sm'>{props.description}</p>
      </div>
    </div>
  )
}

function SectionCard({ section }: { section: GuideSection }) {
  const Icon = section.icon

  return (
    <section id={section.id} className='scroll-mt-24'>
      <Card>
        <CardHeader className='space-y-3'>
          <div className='flex items-center gap-3'>
            <div className='bg-primary/10 text-primary rounded-md p-2'>
              <Icon className='h-5 w-5' />
            </div>
            <CardTitle className='text-xl'>{section.title}</CardTitle>
          </div>
          <p className='text-muted-foreground text-sm leading-6'>
            {section.summary}
          </p>
        </CardHeader>
        <CardContent>
          <ol className='space-y-3'>
            {section.steps.map((step, index) => (
              <li
                key={`${section.id}-${step.title}`}
                className='grid gap-3 sm:grid-cols-[32px_minmax(0,1fr)]'
              >
                <div className='bg-muted text-muted-foreground flex h-8 w-8 items-center justify-center rounded-md text-sm font-semibold'>
                  {index + 1}
                </div>
                <div className='space-y-1'>
                  <h3 className='font-medium'>{step.title}</h3>
                  <p className='text-muted-foreground text-sm leading-6'>
                    {step.body}
                  </p>
                  {step.note && (
                    <p className='text-sm leading-6 text-amber-600'>
                      {step.note}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </section>
  )
}

function QuickNav({
  sections,
  className,
}: {
  sections: GuideSection[]
  className?: string
}) {
  return (
    <div
      className={cn(
        'bg-background/80 supports-[backdrop-filter]:bg-background/70 sticky top-16 z-10 -mx-4 border-y px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6',
        className
      )}
    >
      <div className='mx-auto flex max-w-6xl gap-2 overflow-x-auto'>
        {sections.map((section) => (
          <a
            key={section.id}
            href={`#${section.id}`}
            className='hover:bg-muted whitespace-nowrap rounded-md border px-3 py-2 text-sm'
          >
            {section.title}
          </a>
        ))}
      </div>
    </div>
  )
}

export function UsageGuide() {
  const { t } = useTranslation()
  const origin = getCurrentOrigin()
  const openAiEndpoint = `${origin}/v1`
  const claudeEndpoint = origin

  const sections = useMemo<GuideSection[]>(
    () => [
      {
        id: 'after-purchase',
        title: '购买完成后怎么开始',
        icon: KeyRound,
        summary:
          '充值或兑换成功后，先在主站创建 API Key，再把 Key 填入 Codex、Claude、Chatbox 或其他客户端。',
        steps: [
          {
            title: '确认余额或兑换码',
            body: '如果订单是自动充值，登录主站后检查钱包余额即可；如果拿到兑换码，先进入兑换中心，把兑换码兑换成账户余额。',
          },
          {
            title: '创建 API Key',
            body: '进入 API Keys 页面，创建一个新的 Key，并选择你要使用的分组。Key 只显示一次，创建后请立即保存。',
          },
          {
            title: '选择接入地址',
            body: 'OpenAI/GPT 兼容客户端通常填写 /v1 地址；Claude/Anthropic 兼容客户端通常填写不带 /v1 的站点地址。',
            note: '不同客户端对地址格式要求不同，Claude 地址末尾通常不要加 /v1，GPT/OpenAI 地址必须保留 /v1。',
          },
          {
            title: '先发一条测试消息',
            body: '客户端连通测试通过后，再真正发送一条消息，并回到平台查看用量记录，确认请求已经走到本平台。',
          },
        ],
      },
      {
        id: 'claude-client',
        title: 'Claude 官方客户端接入',
        icon: MessageSquare,
        summary:
          '适合想在 Claude 客户端里使用第三方 Claude API 的用户，核心是填入 API Key 和 Claude 兼容地址。',
        steps: [
          {
            title: '准备 API Key',
            body: '在主站创建并复制 API Key。不要把 Key 发到群聊、截图或公开文档中。',
          },
          {
            title: '填写 Claude 兼容地址',
            body: `API Host 通常填写 ${claudeEndpoint}，不要在末尾额外添加 /v1。`,
          },
          {
            title: '保存后重启客户端',
            body: '客户端保存配置后，建议完全退出并重新打开，再发送测试消息。',
          },
          {
            title: '用用量记录确认',
            body: '模型自我介绍不能证明真实模型。最终以平台用量记录、请求日志和扣费记录为准。',
          },
        ],
      },
      {
        id: 'codex-cc-switch',
        title: 'Codex + CC Switch 接入',
        icon: Route,
        summary:
          '适合第一次配置 Codex 桌面客户端的用户。通过 CC Switch 本地路由，把 Codex 请求转成第三方 API 请求。',
        steps: [
          {
            title: '安装 Codex 和 CC Switch',
            body: '先安装 Codex 桌面客户端和 CC Switch。配置前请彻底退出 Codex，包括后台进程。',
          },
          {
            title: '添加 Codex 自定义供应商',
            body: `在 CC Switch 顶部选择 Codex，点击添加供应商，选择自定义配置。API 请求地址填写 ${openAiEndpoint}。`,
          },
          {
            title: '开启本地路由映射',
            body: '展开高级选项，打开“需要本地路由映射”。Claude、DeepSeek 等模型通常需要本地路由完成协议转换。',
          },
          {
            title: '配置模型映射',
            body: '点击获取模型列表，添加常用模型。菜单显示名可以自定义，实际请求模型必须和平台模型 ID 完全一致。',
            note: '无账号模式下，Codex 可能只显示“自定义”或模型列表为空。把最常用模型放在映射第一行，然后重启 Codex。',
          },
          {
            title: '开启路由并重启 Codex',
            body: '在 CC Switch 路由页面打开路由总开关，并在路由应用中打开 Codex。启用供应商后，彻底重启 Codex。',
          },
          {
            title: '验证真实调用',
            body: '发送一条测试消息后，到平台用量记录里查看 /v1/chat/completions 请求、模型名和消耗。不要只看 Codex 页面里的模型自我介绍。',
          },
        ],
      },
      {
        id: 'chatbox',
        title: 'Chatbox 客户端接入',
        icon: Laptop,
        summary:
          'Chatbox 同时支持 Claude 和 GPT 模型，但两类模型的 API 模式和请求地址不同，需要严格对应。',
        steps: [
          {
            title: '进入模型提供方设置',
            body: '打开 Chatbox，进入左下角设置，在模型提供方里点击添加。',
          },
          {
            title: '添加自定义提供商',
            body: '选择快捷操作里的“添加自定义提供商”，不要直接选择下方 OpenAI 或 Claude 预设项。',
          },
          {
            title: '选择正确 API 模式',
            body: '调用 GPT 模型选择 OpenAI API 兼容；调用 Claude 模型选择 Claude API 兼容。模式选错通常会报接口或模型错误。',
          },
          {
            title: '填写 API Key 与主机',
            body: `GPT/OpenAI 兼容地址填写 ${openAiEndpoint}；Claude 兼容地址填写 ${claudeEndpoint}。API Key 前后不要多出空格。`,
          },
          {
            title: '获取模型并测试',
            body: '点击获取模型列表或发送测试消息。连通测试只代表接口可访问，最终仍需通过平台用量记录确认真实调用。',
          },
        ],
      },
      {
        id: 'troubleshooting',
        title: '常见问题排查',
        icon: AlertTriangle,
        summary:
          '如果客户端显示连通但不能回复，优先检查地址格式、模型 ID、本地路由和 API Key。',
        steps: [
          {
            title: '返回 404 /responses',
            body: '通常是供应商只支持 Chat Completions，但本地路由映射没有开启，或 Codex 没有被 CC Switch 接管。',
          },
          {
            title: '返回 model not found',
            body: '检查实际请求模型是否和平台模型 ID 完全一致，包括大小写、横线、后缀。',
          },
          {
            title: '模型列表为空',
            body: '无账号 API Key 模式下属于常见兼容限制。把要使用的模型放在映射第一行，然后重启客户端。',
          },
          {
            title: '测试成功但没有扣费记录',
            body: '测试接口可能只验证连通性。请真正发送一条消息，再查看平台请求日志和余额变化。',
          },
          {
            title: '国内网络访问失败',
            body: '如果你的站点提供国内直连地址，请改用直连地址；如果使用代理地址，需要确保代理或 TUN/虚拟网卡模式可用。',
          },
        ],
      },
    ],
    [claudeEndpoint, openAiEndpoint]
  )

  return (
    <PublicLayout>
      <div className='space-y-8 pb-10'>
        <section className='space-y-5 pt-4'>
          <div className='flex flex-wrap items-center gap-2'>
            <Badge variant='outline'>{t('Usage guide')}</Badge>
            <Badge variant='secondary'>Claude</Badge>
            <Badge variant='secondary'>Codex</Badge>
            <Badge variant='secondary'>Chatbox</Badge>
          </div>
          <div className='max-w-3xl space-y-3'>
            <h1 className='text-3xl font-semibold tracking-tight sm:text-4xl'>
              买完后，如何使用 Claude 和 Codex
            </h1>
            <p className='text-muted-foreground leading-7'>
              这页把购买、兑换、创建 API Key、客户端接入和故障排查放在一起。
              按顺序走完后，再到平台用量记录里确认真实请求和扣费。
            </p>
          </div>
        </section>

        <Card>
          <CardHeader>
            <div className='flex items-center gap-3'>
              <div className='bg-primary/10 text-primary rounded-md p-2'>
                <Network className='h-5 w-5' />
              </div>
              <CardTitle>常用接入地址</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <EndpointRow
              label='GPT / OpenAI'
              value={openAiEndpoint}
              description='用于 OpenAI API 兼容、Chat Completions、Codex + CC Switch 等场景。'
            />
            <EndpointRow
              label='Claude'
              value={claudeEndpoint}
              description='用于 Claude / Anthropic 兼容客户端，通常不要在末尾添加 /v1。'
              tone='warning'
            />
            <div className='mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800 dark:border-amber-900/70 dark:bg-amber-950/30 dark:text-amber-300'>
              <AlertTriangle className='mt-0.5 h-4 w-4 shrink-0' />
              <p>
                API Key 相当于密码。不要公开截图，不要发到群聊，不要写进公开仓库。
                如果怀疑泄露，请立即删除旧 Key 并重新创建。
              </p>
            </div>
          </CardContent>
        </Card>

        <QuickNav sections={sections} />

        <div className='space-y-6'>
          {sections.map((section) => (
            <SectionCard key={section.id} section={section} />
          ))}
        </div>

        <section className='grid gap-4 rounded-lg border p-4 sm:grid-cols-[auto_minmax(0,1fr)] sm:p-5'>
          <div className='bg-emerald-500/10 text-emerald-600 rounded-md p-2'>
            <ShieldCheck className='h-5 w-5' />
          </div>
          <div className='space-y-2'>
            <h2 className='font-semibold'>最终确认标准</h2>
            <p className='text-muted-foreground text-sm leading-6'>
              客户端能回复只是第一步。真正跑通要同时满足：平台有请求日志、模型 ID
              正确、余额发生对应扣费、没有走到官方账号或其他供应商。
            </p>
            <a
              href='/usage-logs/common'
              className='text-primary inline-flex items-center gap-1 text-sm font-medium hover:underline'
            >
              查看用量记录
              <ExternalLink className='h-3.5 w-3.5' />
            </a>
          </div>
        </section>

        <section className='grid gap-4 rounded-lg border p-4 sm:grid-cols-[auto_minmax(0,1fr)] sm:p-5'>
          <div className='bg-primary/10 text-primary rounded-md p-2'>
            <CheckCircle2 className='h-5 w-5' />
          </div>
          <div className='space-y-2'>
            <h2 className='font-semibold'>没有截图也能操作</h2>
            <p className='text-muted-foreground text-sm leading-6'>
              飞书原文中的截图位置已整理成文字步骤。后续如果要做得更像图文教程，
              可以把截图资源放到站点静态目录，再在这里补成图文版。
            </p>
          </div>
        </section>
      </div>
    </PublicLayout>
  )
}

