"""策划层技能注册表：每个技能 = 输入参数 + 框架要点 + 输出要求。"""
from __future__ import annotations

from typing import Any, Dict

SYSTEM = (
    "你是资深社媒内容策划。只输出最终方案，用中文 Markdown，不解释推理过程。"
    "信息不足时明确写“假设：…”，不要编造数据。所有结论要给可执行动作。"
)

SKILLS: Dict[str, Dict[str, Any]] = {
    "plan_positioning": {
        "name": "差异化定位",
        "description": "找到「我和别人不一样且用户需要」的位置。当用户问「我的定位是什么/怎么和竞品区分/同质化怎么办」时使用；拆解具体竞品账号用 online.discover_competitor。",
        "keywords": ["差异化定位", "我的定位", "怎么和竞品区分", "同质化", "找差异"],
        "args": {"account": {"type": "string"}, "competitors": {"type": "array", "items": {"type": "string"}}, "goal": {"type": "string"}},
        "required": ["account"],
        "guide": """1) 赛道共性：主流玩家在做什么
2) 定位坐标：给出两个关键维度并说明竞品分布与空白区
3) 空白机会：人群/场景/价值/形式 四类，排除“伪空白”
4) 差异化主张：选 1-2 个最难被模仿的点
5) 一句话定位：为[人群]在[场景]提供[独特价值]的[品类]
6) 落地：选题/风格/视觉/人设如何体现；给 3 个验证信号""",
        "output": "## 赛道共性 / ## 定位坐标 / ## 空白机会 / ## 差异化主张 / ## 一句话定位 / ## 落地建议",
    },
    "plan_audience": {
        "name": "受众画像",
        "description": "分析粉丝人群特征、痛点、内容偏好与触达渠道。",
        "keywords": ["受众画像", "粉丝是谁", "写给谁", "目标人群"],
        "args": {"account": {"type": "string"}, "product": {"type": "string"}, "platforms": {"type": "array", "items": {"type": "string"}}},
        "required": ["account"],
        "guide": """1) 人群切片：年龄/城市/职业/消费力（标注假设）
2) 三层需求：表层想要 / 真实痛点 / 未被满足的渴望
3) 决策触发：什么时候会点开、会收藏、会私信
4) 内容偏好：形式、时长、语气、反感点
5) 触达渠道：主平台 + 搜索词 + 社群
6) 输出一张受众画像卡 + 3 条内容切入点""",
        "output": "## 画像卡 / ## 三层需求 / ## 决策触发 / ## 内容偏好 / ## 触达渠道 / ## 切入点",
    },
    "plan_voice": {
        "name": "人设声音",
        "description": "从样本与访谈提炼语气/用词/节奏，保证内容风格一致。",
        "keywords": ["人设声音", "语气", "不像我说话", "风格一致", "口吻"],
        "args": {"samples": {"type": "array", "items": {"type": "string"}}, "account": {"type": "string"}, "notes": {"type": "string"}},
        "required": [],
        "guide": """1) 语气：温暖/犀利/专业/幽默，各给一句示例
2) 用词：高频词、禁用词、行业黑话处理
3) 节奏：句长、段落、口头禅
4) 结构偏好：先结论还是先故事
5) 三条改写对照：普通写法 → 你的写法
6) 一段 80 字风格样例""",
        "output": "## 语气 / ## 用词 / ## 节奏 / ## 改写对照 / ## 风格样例",
    },
    "plan_account_diagnosis": {
        "name": "账号诊断",
        "description": "诊断垂直度、定位清晰度、限流信号、流量池阶段并给处方。",
        "keywords": ["账号诊断", "起号体检", "没流量", "被限流", "垂直度"],
        "args": {"account": {"type": "string"}, "metrics": {"type": "string"}, "recent": {"type": "string"}},
        "required": ["account"],
        "guide": """1) 症状：播放/互动/涨粉三段数据表现
2) 归因：垂直度、定位、内容结构、发布时间、违规风险
3) 证据：每条结论对应可观察信号
4) 处方：按优先级 3-5 条，本周可执行
5) 预期与验证：多久见效、看哪个指标""",
        "output": "## 症状 / ## 归因 / ## 证据 / ## 处方 / ## 验证",
    },
    "plan_article_outline": {
        "name": "长文大纲",
        "description": "生成 H2/H3 结构、字数目标、图表位与 FAQ。",
        "keywords": ["文章大纲", "长文结构", "公众号大纲", "知乎专栏"],
        "args": {"topic": {"type": "string"}, "platform": {"type": "string"}, "keywords": {"type": "array", "items": {"type": "string"}}},
        "required": ["topic"],
        "guide": """1) 一句话主旨 + 目标读者
2) H2/H3 结构（6-9 个 H2），每节写目的与字数
3) 图表/案例位置
4) FAQ 3-5 条
5) 开头 2 版 + 结尾 CTA""",
        "output": "## 主旨 / ## 结构 / ## 图表位 / ## FAQ / ## 开头与结尾",
    },
    "plan_campaign": {
        "name": "活动营销策划",
        "description": "节日/大促/新品活动的目标、节奏、矩阵、预算与指标。",
        "keywords": ["活动策划", "大促", "618", "双11", "新品发布", "节日营销"],
        "args": {"occasion": {"type": "string"}, "product": {"type": "string"}, "budget": {"type": "string"}, "platforms": {"type": "array", "items": {"type": "string"}}},
        "required": ["occasion", "product"],
        "guide": """1) 目标拆解：曝光/互动/转化三层指标
2) 节奏：预热-爆发-返场，各阶段内容与频次
3) 多平台矩阵：每个平台的角色与形式
4) 互动玩法与 KOL 分层
5) 预算分配比例
6) 风险与合规提醒""",
        "output": "## 目标 / ## 节奏 / ## 平台矩阵 / ## 玩法 / ## 预算 / ## 风险",
    },
    "plan_carousel": {
        "name": "轮播图规划",
        "description": "规划多图笔记/轮播的分页结构、每页文案与视觉方向。",
        "keywords": ["轮播", "多图笔记", "分页结构", "封面钩子"],
        "args": {"topic": {"type": "string"}, "platform": {"type": "string"}, "pages": {"type": "integer"}},
        "required": ["topic"],
        "guide": """1) 封面：钩子文案 + 视觉方向
2) 逐页：页码/标题/正文（≤40字）/视觉
3) 节奏：信息密度与翻页动机
4) CTA 与互动引导
5) 合规与敏感词检查""",
        "output": "## 封面 / ## 分页表 / ## 节奏 / ## CTA",
    },
    "plan_collab": {
        "name": "合作提案",
        "description": "品牌合作/联名方案：权益、报价、交付与风险。",
        "keywords": ["品牌合作", "商单", "联名", "报价", "合作方案"],
        "args": {"brand": {"type": "string"}, "audience": {"type": "string"}, "budget": {"type": "string"}},
        "required": ["brand"],
        "guide": """1) 合作背景与双方收益
2) 内容形式与交付清单
3) 报价区间与计价依据（粉丝量/互动率/形式）
4) 排期与审核流程
5) 风险与免责条款""",
        "output": "## 背景 / ## 交付 / ## 报价 / ## 排期 / ## 风险",
    },
    "plan_calendar": {
        "name": "月度内容排期",
        "description": "生成一个月的发布排期（逐条选题+角度+形式）。当用户问「这个月发什么/内容日历/排期表」时使用；只想看节点用 online.discover_calendar。",
        "keywords": ["内容排期", "月度日历", "发布计划", "排期", "怎么排", "这个月发什么"],
        "args": {"month": {"type": "string"}, "pillars": {"type": "array", "items": {"type": "string"}}, "platforms": {"type": "array", "items": {"type": "string"}}, "frequency": {"type": "string"}},
        "required": [],
        "guide": """1) 本月目标与节奏（周更/日更）
2) 支柱配比与形式搭配
3) 逐条排期表：日期/平台/支柱/选题标题/角度/形式/目的
4) 禁止出现“发一条教程”这类模糊条目
5) 节点借势与断更风险""",
        "output": "## 月度目标 / ## 排期表 / ## 节点借势 / ## 风险",
    },
    "plan_matrix": {
        "name": "选题矩阵",
        "description": "内容支柱 × 8 种格式，每格一个可执行标题。",
        "keywords": ["选题矩阵", "批量选题", "选题池", "支柱乘格式"],
        "args": {"pillars": {"type": "array", "items": {"type": "string"}}, "formats": {"type": "array", "items": {"type": "string"}}},
        "required": ["pillars"],
        "guide": """格式固定 8 种：Actionable / Motivational / Analytical / Contrarian / Observation / X vs Y / Present vs Future / Listicle
1) 输出 Markdown 表格：行=支柱，列=格式，格子=具体标题
2) 标题要具体可写，不要抽象名词
3) 表格后给 Top 8 推荐并说明理由""",
        "output": "## 选题矩阵表 / ## Top 8 推荐",
    },
    "plan_strategy": {
        "name": "内容策略",
        "description": "支柱架构、受众路径、90 天节奏、渠道与 KPI。",
        "keywords": ["内容策略", "内容支柱", "90天规划", "账号规划"],
        "args": {"account": {"type": "string"}, "goal": {"type": "string"}, "platforms": {"type": "array", "items": {"type": "string"}}},
        "required": ["account"],
        "guide": """1) 内容支柱 3-5 个，各写边界与配比
2) 受众路径：认知-兴趣-信任-转化
3) 90 天节奏：起量/放大/变现三阶段
4) 渠道分工
5) KPI 与复盘周期""",
        "output": "## 支柱 / ## 受众路径 / ## 90天节奏 / ## 渠道 / ## KPI",
    },
    "plan_hook": {
        "name": "开头钩子",
        "description": "给主题生成 6 种开头钩子（两行、每行≤40字）。当用户问「写钩子/开头怎么抓人/前三秒」时使用；成篇文案用创作层，不在这里。",
        "keywords": ["钩子", "开头", "Hook", "前三秒", "标题钩子"],
        "args": {"topic": {"type": "string"}, "platform": {"type": "string"}},
        "required": ["topic"],
        "guide": """严格按以下格式输出，不要多余解释：
### 1. 数字领衔
> 开场（≤40字）
> 反转（≤40字）
### 2. 逆向认知
### 3. 个人蜕变
### 4. 权威借势
### 5. 自我坦白
### 6. 未来冲击
每条两行，必须有反转感。""",
        "output": "6 个小节，每节两行引用（> 开头 / > 反转）",
    },
    "plan_livestream": {
        "name": "直播方案",
        "description": "主题、流程时间表、话术与互动设计。",
        "keywords": ["直播方案", "直播流程", "话术", "带货直播"],
        "args": {"topic": {"type": "string"}, "duration": {"type": "string"}, "goal": {"type": "string"}},
        "required": ["topic"],
        "guide": """1) 主题与目标
2) 流程时间表（开场/讲解/互动/催单/收尾）
3) 话术：开场白、过渡、催单、感谢、答疑
4) 互动设计：抽奖/提问/连麦
5) 合规红线""",
        "output": "## 主题 / ## 流程表 / ## 话术 / ## 互动 / ## 合规",
    },
    "plan_topic_eval": {
        "name": "选题评估",
        "description": "对单个已有选题做七维打分并给「做/改/不做」。当用户问「这个选题值不值得做/能不能火」时使用；还没选题、要一批标题请用 plan_matrix。",
        "keywords": ["选题评估", "值不值得做", "能不能火", "做不做"],
        "args": {"topic": {"type": "string"}, "account": {"type": "string"}, "platform": {"type": "string"}},
        "required": ["topic"],
        "guide": """先输出严格 JSON：
{"scores":{"流量潜力":n,"账号匹配":n,"竞争差异化":n,"时效价值":n,"变现潜力":n,"制作成本":n,"合规风险":n},
 "reasons":{"维度":"一句话证据"},"alternatives":["替代选题1","替代选题2"]}
分数 1-10；制作成本与合规风险为反向（高分=低成本/低风险）。
JSON 后再给 3 条优化建议。""",
        "output": "JSON（含 scores/reasons/alternatives）+ 优化建议",
    },
    "plan_trend": {
        "name": "热点借势",
        "description": "给定热点结合账号定位出切入角度、形式、标题与风险。",
        "keywords": ["热点怎么蹭", "借势", "追热点", "热点方案"],
        "args": {"hotspot": {"type": "string"}, "account": {"type": "string"}},
        "required": ["hotspot"],
        "guide": """1) 热点与账号的连接点（3 个角度）
2) 推荐形式与时长
3) 3 个标题（含钩子）
4) 时效窗口
5) 风险与不适合蹭的情况""",
        "output": "## 连接点 / ## 形式 / ## 标题 / ## 时效 / ## 风险",
    },
    "plan_brand_onboarding": {
        "name": "品牌/账号入驻",
        "description": "结构化访谈整理视觉风格、调性、受众与目标，产出画像档案。",
        "keywords": ["账号入驻", "品牌建档", "账号画像", "新号开始"],
        "args": {"brand": {"type": "string"}, "industry": {"type": "string"}, "audience": {"type": "string"}, "goals": {"type": "string"}},
        "required": ["brand"],
        "guide": """1) 定位一句话
2) 视觉：色彩/字体/版式
3) 调性：语气与禁忌
4) 受众与场景
5) 运营目标与 KPI
6) 内容支柱初稿""",
        "output": "## 定位 / ## 视觉 / ## 调性 / ## 受众 / ## 目标 / ## 支柱",
    },
}
