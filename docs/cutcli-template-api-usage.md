# 模板定制服务器接口使用说明

本文说明 `lobster_server` 上 CutCLI 模板定制相关接口的调用方式。接口只说明客户端/在线端如何使用服务器能力，不包含部署密钥、Token 或服务器私有配置。

## 基础约定

- 基础地址：生产环境使用实际服务器域名，例如 `https://bhzn.top`。
- 鉴权：所有接口都需要登录用户 Bearer Token。
- 请求头：

```http
Authorization: Bearer <user_jwt>
```

- 模板渲染涉及视频输入。输入来源三选一：
  - `file`：直接上传本地视频文件。
  - `asset_id`：使用当前用户素材库里的视频素材。
  - `video_url`：使用公网可访问的视频 URL。
- `audio_url` 可选。传入后服务端使用该音频做 STT；不传则从输入视频提取音频。
- `position_overrides` 可选，必须是 JSON 字符串，用于微调字幕和叠加文案位置。

## 1. 获取模板列表

```http
GET /api/cutcli/templates
Authorization: Bearer <user_jwt>
```

返回示例：

```json
{
  "ok": true,
  "templates": [
    {
      "id": "auto_caption_neon_focus_v1",
      "kind": "auto_caption",
      "name": "霓虹聚焦字幕",
      "description": "...",
      "aspect_ratio": "source",
      "input_modes": ["upload", "asset_id"],
      "preserve_source_video": true,
      "preview_url": "/client/client-code/cutcli_templates/auto_caption_neon_focus_v1.mp4",
      "sample_video_url": "/client/client-code/cutcli_templates/auto_caption_neon_focus_v1.mp4",
      "render_path": "/api/cutcli/templates/auto_caption_neon_focus_v1/render",
      "render_modes": ["ffmpeg", "cutcli_cloud"],
      "overlay_fields": [],
      "caption_style": {},
      "generation_strategy": {
        "version": 1,
        "executor": "online",
        "stt": {
          "provider": "sutui",
          "model": "volcengine/speech-to-text/bigmodel-v2",
          "server_endpoint": "/api/cutcli/stt/transcribe",
          "input": "audio_url"
        },
        "cloud_render_endpoint": "/api/cutcli/cloud/render-draft"
      }
    }
  ]
}
```

客户端建议：

- 以服务器返回的 `templates` 为准，不在客户端写死模板。
- 使用 `render_path` 发起渲染。
- 使用 `caption_style`、`overlay_fields`、`generation_strategy` 做预览和渲染参数参考。

## 2. 发起模板渲染

推荐使用模板列表返回的 `render_path`：

```http
POST /api/cutcli/templates/{template_id}/render
Authorization: Bearer <user_jwt>
Content-Type: multipart/form-data
```

也可以使用通用入口：

```http
POST /api/cutcli/templates/render
Authorization: Bearer <user_jwt>
Content-Type: multipart/form-data
```

通用入口需要额外传 `template_id`。

表单字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `template_id` | string | 通用入口必填 | 模板 ID。路径入口不用传。 |
| `file` | file | 三选一 | 上传视频文件。 |
| `asset_id` | string | 三选一 | 当前用户素材库视频 ID。 |
| `video_url` | string | 三选一 | 公网视频 URL。 |
| `audio_url` | string | 否 | 公网音频 URL；传入后用于 STT。 |
| `position_overrides` | string | 否 | JSON 字符串，用于位置微调。 |
| `title` | string | 否 | 旧版非 auto_caption 模板标题。 |
| `subtitle` | string | 否 | 旧版非 auto_caption 模板副标题。 |
| `duration_seconds` | int | 否 | 旧版非 auto_caption 模板时长。 |

上传文件示例：

```bash
curl -X POST "https://bhzn.top/api/cutcli/templates/auto_caption_neon_focus_v1/render" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@demo.mp4" \
  -F 'position_overrides={"caption_position":{"x":0,"y":-0.72}}'
```

使用素材库 `asset_id` 示例：

```bash
curl -X POST "https://bhzn.top/api/cutcli/templates/auto_caption_neon_focus_v1/render" \
  -H "Authorization: Bearer $TOKEN" \
  -F "asset_id=abc123def456"
```

使用公网视频 URL 示例：

```bash
curl -X POST "https://bhzn.top/api/cutcli/templates/auto_caption_neon_focus_v1/render" \
  -H "Authorization: Bearer $TOKEN" \
  -F "video_url=https://cdn.example.com/input.mp4" \
  -F "audio_url=https://cdn.example.com/audio.wav"
```

auto-caption 模板返回异步任务：

```json
{
  "ok": true,
  "async": true,
  "job_id": "20260606123000_ab12cd34",
  "status": "running",
  "stage": "created",
  "template_id": "auto_caption_neon_focus_v1",
  "poll_path": "/api/cutcli/templates/jobs/20260606123000_ab12cd34",
  "preserve_source_video": true
}
```

客户端收到 `async: true` 后，使用 `poll_path` 查询任务状态。

## 3. 查询模板渲染任务列表

```http
GET /api/cutcli/templates/jobs?limit=50
Authorization: Bearer <user_jwt>
```

说明：

- 只返回当前登录用户自己的任务。
- `limit` 范围为 1 到 100，默认 50。
- 返回字段与任务详情一致。

## 4. 查询模板渲染任务详情

```http
GET /api/cutcli/templates/jobs/{job_id}
Authorization: Bearer <user_jwt>
```

处理中示例：

```json
{
  "ok": true,
  "async": true,
  "job_id": "20260606123000_ab12cd34",
  "status": "running",
  "stage": "stt_poll",
  "preview_url": "",
  "open_url": "",
  "error": "",
  "poll_path": "/api/cutcli/templates/jobs/20260606123000_ab12cd34"
}
```

完成示例：

```json
{
  "ok": true,
  "async": true,
  "job_id": "20260606123000_ab12cd34",
  "status": "completed",
  "stage": "completed",
  "template_id": "auto_caption_neon_focus_v1",
  "preview_asset_id": "abc123def456",
  "final_asset_id": "abc123def456",
  "preview_url": "https://cdn.example.com/assets/cutcli_auto_caption/20260606123000_ab12cd34/final.mp4",
  "open_url": "https://cdn.example.com/assets/cutcli_auto_caption/20260606123000_ab12cd34/final.mp4",
  "caption_count": 18,
  "render_strategy": "ffmpeg",
  "warnings": [],
  "error": ""
}
```

失败示例：

```json
{
  "ok": true,
  "async": true,
  "job_id": "20260606123000_ab12cd34",
  "status": "failed",
  "stage": "failed",
  "error_code": "stt_timeout",
  "error": "STT task timed out: task_xxx"
}
```

客户端建议：

- `status` 为 `completed` 时使用 `open_url` 或 `preview_url` 展示成片。
- `status` 为 `failed` 时展示 `error`，必要时显示 `error_code` 便于排查。
- 轮询间隔建议 2 到 5 秒。

## 5. 服务端 STT 辅助接口

当客户端自行拆出音频并希望只让服务器做 STT，可调用：

```http
POST /api/cutcli/stt/transcribe
Authorization: Bearer <user_jwt>
Content-Type: application/json
```

请求体：

```json
{
  "audio_url": "https://cdn.example.com/audio.wav",
  "return_captions": true,
  "video_duration_sec": 12.4,
  "video_width": 1080,
  "caption_style": {}
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `audio_url` | 是 | 公网可访问音频 URL。 |
| `return_captions` | 否 | 是否按 `caption_style` 直接生成字幕片段。 |
| `video_duration_sec` | 否 | 视频总时长，生成字幕片段时用于边界处理。 |
| `video_width` | 否 | 视频宽度，生成字幕片段时用于安全换行。 |
| `caption_style` | 否 | 模板列表返回的字幕样式。 |

## 6. 云渲染草稿辅助接口

当客户端已经生成 CutCLI 草稿 zip，并希望服务器使用服务端 Token 调云渲染，可调用：

```http
POST /api/cutcli/cloud/render-draft
Authorization: Bearer <user_jwt>
Content-Type: application/json
```

请求体：

```json
{
  "draft_id": "draft_xxx",
  "draft_zip_url": "https://cdn.example.com/draft.zip",
  "timeout_seconds": 1800,
  "mirror_to_tos": true
}
```

返回：

```json
{
  "ok": true,
  "job_id": "20260606123000_ab12cd34",
  "draft_id": "draft_xxx",
  "cloud_job_id": "cloud_task_xxx",
  "preview_url": "https://cdn.example.com/assets/cutcli_auto_caption/20260606123000_ab12cd34/final.mp4",
  "open_url": "https://cdn.example.com/assets/cutcli_auto_caption/20260606123000_ab12cd34/final.mp4",
  "raw_preview_url": "https://upstream.example.com/video.mp4",
  "file_size": 12345678,
  "warnings": []
}
```

说明：

- `draft_zip_url` 必须是服务器可下载的公网 URL。
- `mirror_to_tos=true` 时，服务器会把上游视频转存到配置的 TOS/CDN，再返回稳定公网链接。

## 7. `position_overrides` 示例

`position_overrides` 是 JSON 字符串，渲染接口用 `multipart/form-data` 提交时要作为普通字段传入。

常用字段示例：

```json
{
  "caption_position": {"x": 0, "y": -0.72},
  "transform_x": 0,
  "transform_y": -0.72,
  "font_size": 11,
  "ass_font_size": 58,
  "overlay_style": {
    "headline_y_ratio": 0.18,
    "badge_y_ratio": 0.58
  }
}
```

注意：

- 坐标通常为归一化位置，具体效果要以模板 `caption_style` 为准。
- 横屏/竖屏会自动应用模板内的 `orientation_styles`。
- 客户端预览应尽量使用服务器返回的 `caption_style` 和 `generation_strategy`，保证预览与成片一致。

## 8. 常见错误

| HTTP 状态 | 场景 | 处理建议 |
| --- | --- | --- |
| 401 | 未带 Bearer Token 或 Token 无效 | 重新登录并刷新 Token。 |
| 400 | `audio_url` / `video_url` / `draft_zip_url` 不是 http(s) URL | 确保 URL 为服务器可访问的公网链接。 |
| 404 | 模板 ID 或任务 ID 不存在 | 重新拉取模板列表，或检查任务是否属于当前用户。 |
| 500 | STT、云渲染、ffmpeg/TOS 等执行失败 | 查看响应 `detail.code`、`detail.message`，必要时查服务器日志。 |

## 9. 客户端推荐流程

1. 登录后拿用户 Token。
2. `GET /api/cutcli/templates` 拉取模板列表并渲染模板选择界面。
3. 用户选择模板和输入视频。
4. 调用模板的 `render_path`。
5. 若返回 `async: true`，按 `poll_path` 轮询。
6. `status=completed` 后展示 `open_url`，并把 `final_asset_id` 或 `preview_asset_id` 作为素材库引用。
7. 若用户调整位置，重新提交 `position_overrides` 渲染。
