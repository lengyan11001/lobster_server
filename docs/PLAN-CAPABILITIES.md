# 策划层能力（16 个）

服务端执行（task_kind=server_action, side_effect=read），调用内部模型生成；
评分、字数校验等可计算部分由脚本完成。

| 能力 | 用途 | 关键参数 |
|---|---|---|
| plan_positioning | 差异化定位（赛道/坐标/空白/一句话） | account, competitors[], goal |
| plan_audience | 受众画像（三层需求/触发/渠道） | account, product, platforms[] |
| plan_voice | 人设声音（语气/用词/节奏/改写样例） | samples[], account, notes |
| plan_account_diagnosis | 账号体检（症状→归因→处方） | account, metrics, recent |
| plan_article_outline | 长文大纲（H2/H3/字数/FAQ） | topic, platform, keywords[] |
| plan_campaign | 活动营销（节奏/矩阵/预算/风险） | occasion, product, budget, platforms[] |
| plan_carousel | 轮播分页（封面/逐页/CTA） | topic, platform, pages |
| plan_collab | 合作提案（交付/报价/风险） | brand, audience, budget |
| plan_calendar | 月度排期（逐条具体） | month, pillars[], platforms[], frequency |
| plan_matrix | 选题矩阵（支柱 × 8 格式） | pillars[], formats[] |
| plan_strategy | 内容策略（支柱/路径/90天/KPI） | account, goal, platforms[] |
| plan_hook | 6 种 Hook（两行 ≤40 字） | topic, platform |
| plan_livestream | 直播方案（流程/话术/互动） | topic, duration, goal |
| plan_topic_eval | 七维评分 + 做/改/不做 | topic, account, platform |
| plan_trend | 热点借势（角度/形式/标题/风险） | hotspot, account |
| plan_brand_onboarding | 品牌/账号建档 | brand, industry, audience, goals |

## 确定性部分
- `plan_topic_eval`：模型只输出七维分数与理由，加权分与结论由 `plan/scoring.py` 计算（权重 25/20/15/15/10/8/7，阈值 70/50）
- `plan_hook`：脚本校验每行 ≤40 字，超限列出

## 模型与降级
- 默认 `deepseek-v4-pro`，不可用时依次退 `deepseek-v4.1-flash`、`glm-5.3`
- 全部不可用：返回"策划生成失败"，不编造内容
- 单次生成约 30-60 秒

## 已知限制
- 输出质量依赖账号信息完整度；缺 Profile 时会在开头标注"假设"
- 排期/矩阵是文本方案，尚未写入排期表（后续接 ip_content_schedule_templates）
