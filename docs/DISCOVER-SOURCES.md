# 发现层数据源策略（免费优先）

> 原则：**默认只用免费公开源**；付费 API（如 TikHub）不进入默认链路，也不作为文档首选。
> 需要更强数据时再由业务方显式开启，并单独计费。

## 当前免费源与状态（2026-10-27 实测）

| 平台 | 主源 | 备源 | 实测状态 |
|---|---|---|---|
| 微博 | `60s.viki.moe/v2/weibo` | `v2.xxapi.cn/api/weibohot` | 60s 偶发 429，备源可用 |
| 抖音 | `60s.viki.moe/v2/douyin` | `v2.xxapi.cn/api/douyinhot` | 可用 |
| 头条 | `60s.viki.moe/v2/toutiao` | — | 可用 |
| 知乎 | `60s.viki.moe/v2/zhihu` | — | 端点返回空（上游问题） |
| 百度 | `60s.viki.moe/v2/baidu/hot` | `v2.xxapi.cn/api/baiduhot` | 可用 |
| B站 | `60s.viki.moe/v2/bili` | `v2.xxapi.cn/api/bilibilihot` | 60s 不稳，备源可用 |
| 小红书 | `60s.viki.moe/v2/rednote` | — | 500，暂无免费源 |

## 降级链（代码已实现）

1. 60s v2 主源
2. xxapi 备源（仅微博/抖音/百度/B站）
3. 都失败 → 返回 `errors`，如实告知用户，不编造

缓存：热搜 5 分钟，资讯 30 分钟。

## 竞品 / UGC / 账号数据的现实边界

免费方案下只能：
- 抓平台公开页（常被反爬，返回验证码）
- 用公开搜索补全（`web_search` / `site:` 语法）
- 抽 10–20 条样本，明确标注"基于样本，非全量"

**拿不到且不要编造**：播放量、完播率、粉丝增量、转化、私域数据（在创作者中心）。

## 付费源（默认关闭）

TikHub 等付费 API 只在显式配置时启用，用于补齐：
- 小红书 / 微博 / 知乎 的账号与内容数据
- 评论、UGC 明细
- 海外平台（LinkedIn / Reddit / Twitter）

启用前需确认额度与计费，文档与能力描述里**不默认推荐**。

## 已实现模块（2026-10-27）

| 能力 | 数据来源 | 说明 |
|---|---|---|
| `online.discover_hotlist` | 60s / xxapi | 多平台热搜，5 分钟缓存 |
| `online.discover_news` | 8 个 RSS 源 | 按赛道路由，30 分钟缓存 |
| `online.discover_rss` | 用户指定 feed | 标准库解析 |
| `online.discover_calendar` | 本地 events_china.md + lunar.py | 农历离线换算，无网络依赖 |
| `online.discover_gap` | 热搜 + Bing/百度联想词 | 加权评分（30/25/20/15/10） |
| `online.discover_competitor` | 直连 / Jina / 无头浏览器 | 公开页可见计数，缺失指标明确标注 |
| `online.discover_ugc` | Bing 公开搜索 | 提及链接列表，互动明细需人工补 |

| `online.discover_algorithm` | 本地知识库（algorithm-vocabulary + platform-sources） | 平台推荐机制/限流/官方公告来源，离线 |
| `online.discover_platform_diff` | 本地知识库（platform-traits） | 跨平台格式/节奏/合规差异 |

移植完成度：9/9（Easel 发现层全部技能已落地；知识型技能用本地文档实现，不依赖网络）。
