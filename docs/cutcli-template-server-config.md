# 模板服务器侧配置说明（不依赖客户端 OTA）

模板=服务器数据，客户端只负责渲染。新增/修改模板只需改服务器配置，**不用发客户端 OTA**。

## 1. 配置在哪里

| 文件 | 用途 | 是否进 git |
| --- | --- | --- |
| `data/cutcli_templates/designer_templates.json` | 正式模板清单，随服务端代码部署 | 是 |
| `data/cutcli_templates/designer_templates.local.json` | 服务器本地覆盖/临时新增，按 `id` 覆盖同名项，新 id 直接追加 | 否（已 gitignore） |

两个文件都是 JSON 数组，元素形如：

```json
{
  "id": "designer_33",
  "kind": "auto_caption",
  "name": "模板名",
  "description": "一句话描述",
  "aspect_ratio": "9:16",
  "tags": ["口播", "竖屏"],
  "preview_url": "https://.../designer_33.mp4",
  "sample_video_url": "https://.../designer_33.mp4",
  "overlay_fields": [{"key": "text_1", "label": "默认文案", "default": "默认文案"}],
  "caption_style": {
    "id": "designer_33",
    "ass_layout": "designer_timeline",
    "overlay_style": {
      "layout": "designer_timeline",
      "text_tracks": [
        {"key": "text_1", "start_ms": 0, "end_ms": 1500000, "x": 0.5, "y": 0.78,
         "font_family": "阿里巴巴普惠体 3.0 75 SemiBold", "font_size": 66,
         "color": "&H00000000", "default": "默认文案"}
      ]
    }
  },
  "meta": {
    "skip_stt": true,
    "target_width": 1280,
    "target_height": 2276,
    "font_url": "https://.../fonts/xxx.otf",
    "font_filename": "xxx.otf",
    "font_sha256": "...",
    "preview_poster_url": "https://.../designer_33.jpg"
  }
}
```

## 2. 生效方式

- 清单按 **mtime 热加载**：保存文件后下一次请求 `GET /api/cutcli/templates` 就生效，**不用重启服务**。
- 首次出现的 `id` 自动入库；已有 `id` 的名字/封面/文案/样式/metadata 有变化会自动同步（不需要 bump `style_version`）。
- 从清单里删掉某个 `id`（曾在清单里出现过）→ 该模板自动 `enabled=false` 下架，数据库记录保留。
- `sort_order` 可选，未填时按清单顺序排在后面。

## 3. 素材

- 预览视频/封面：上传 TOS，URL 直接写 `preview_url` / `sample_video_url` / `meta.preview_poster_url`。建议 540p、24fps、无音轨、faststart，单体 1MB 上下。
- 字体：写 `meta.font_url` + `font_filename` + `font_sha256`。客户端渲染时按需下载并校验 sha256，缓存在 `%LOCALAPPDATA%\lobster\fonts`，**字体也不需要进客户端包**。下载或校验失败直接报错，不静默降级。

## 4. 客户端为什么不用发版

- 模板列表实时走 `GET /api/cutcli/templates`，客户端无本地缓存、无硬编码模板 id。
- 版式由 `caption_style.overlay_style.layout`（如 `designer_timeline`）驱动，字段和坐标都在服务端下发。
- 只有“新增一种全新版式类型”才需要改客户端代码；换模板、换文案、换字体、换封面都不需要。
