# 模型参数审计报告

**审计时间**: 2026-03-20 07:01:42 UTC
**审计模型数**: 76

> 方法：仅 image/video；按 xskill `params_schema` 构造探测 payload；校验 `_normalize_*_payload` 输出；枚举支持 int/字符串互通；见 `docs/参数审计方法对比.md`。

## 问题统计

- 🔴 CRITICAL: 24 个
- 🟠 HIGH: 0 个
- 🟡 MEDIUM: 0 个
- 🟢 LOW: 0 个

## 详细问题清单

### CRITICAL 级别问题 (24 个)

#### fal-ai/kling-video/v3/standard/motion-control - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '参考视频 URL。动作来源视频，需包含真实风格角色的全身或上半身（含头部），无遮挡', 'examples': ['https://cdn-video.51sux.com/kling-v3-examples/mc_std_video.mp4']}
- **位置**: mcp/http_server.py

#### fal-ai/kling-video/v3/standard/motion-control - character_orientation
- **问题类型**: missing_required
- **描述**: 必填参数 character_orientation 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': "角色朝向控制。'video': 朝向匹配参考视频（适合复杂动作，最长30秒）；'image': 朝向匹配参考图片（适合跟随摄像机移动，最长10秒）", 'enum': ['image', 'video']}
- **位置**: mcp/http_server.py

#### fal-ai/kling-video/v3/pro/motion-control - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '参考视频 URL。动作来源视频，需包含真实风格角色的全身或上半身（含头部），无遮挡', 'examples': ['https://cdn-video.51sux.com/kling-v3-examples/mc_pro_video.mp4']}
- **位置**: mcp/http_server.py

#### fal-ai/kling-video/v3/pro/motion-control - character_orientation
- **问题类型**: missing_required
- **描述**: 必填参数 character_orientation 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': "角色朝向控制。'video': 朝向匹配参考视频（适合复杂动作，最长30秒）；'image': 朝向匹配参考图片（适合跟随摄像机移动，最长10秒）", 'enum': ['image', 'video']}
- **位置**: mcp/http_server.py

#### fal-ai/kling-video/v2.6/standard/motion-control - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '参考视频 URL。动作来源视频，需包含真实风格角色的全身或上半身（含头部），无遮挡', 'examples': ['https://cdn-video.51sux.com/playground/20260201/ddca2961-741b-40d5-9115-0351edd3af39.mp4']}
- **位置**: mcp/http_server.py

#### fal-ai/kling-video/v2.6/standard/motion-control - character_orientation
- **问题类型**: missing_required
- **描述**: 必填参数 character_orientation 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': "角色朝向控制。'video': 朝向匹配参考视频（适合复杂动作，最长30秒）；'image': 朝向匹配参考图片（适合跟随摄像机移动，最长10秒）", 'enum': ['image', 'video'], 'examples': ['video']}
- **位置**: mcp/http_server.py

#### fal-ai/kling-video/o3/standard/video-to-video/reference - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '参考视频 URL（仅 .mp4/.mov 格式，3-10 秒时长，720-2160px 分辨率，最大 200MB）', 'examples': ['https://cdn-video.51sux.com/kling-o3-examples/std_v2v_ref_video.mp4']}
- **位置**: mcp/http_server.py

#### fal-ai/kling-video/o3/pro/video-to-video/reference - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '参考视频 URL（仅 .mp4/.mov 格式，3-10 秒时长，720-2160px 分辨率，最大 200MB）', 'examples': ['https://cdn-video.51sux.com/kling-o3-examples/pro_v2v_ref_video.mp4']}
- **位置**: mcp/http_server.py

#### fal-ai/bytedance/seedream/v4.5/edit - image_urls
- **问题类型**: missing_required
- **描述**: 必填参数 image_urls 未在 normalize 结果中或为空
- **期望**: {'type': 'array', 'description': '输入图片 URL 列表（1-10 张）', 'examples': [['https://cdn-video.51sux.com/model-examples/20260202/model-examples-orange-cat.png']]}
- **位置**: mcp/http_server.py

#### fal-ai/bytedance/seedream/v5/lite/edit - image_urls
- **问题类型**: missing_required
- **描述**: 必填参数 image_urls 未在 normalize 结果中或为空
- **期望**: {'type': 'array', 'description': '输入图片 URL 列表（1-10 张）', 'examples': [['https://cdn-video.51sux.com/seedream-v5-lite-examples/seedream_v5_lite_edit_product.png', 'https://cdn-video.51sux.com/seedream-v5-lite-examples/seedream_v5_lite_edit_replacement.png', 'https://cdn-video.51sux.com/seedream-v5-lite-examples/seedream_v5_lite_edit_logo.png']]}
- **位置**: mcp/http_server.py

#### fal-ai/sora-2/text-to-video - model
- **问题类型**: invalid_enum
- **描述**: 参数 model 不在 schema 枚举中（已做 int/str 兼容比较）
- **期望**: ['sora-2', 'sora-2-2025-12-08', 'sora-2-2025-10-06']
- **实际**: fal-ai/sora-2/text-to-video
- **位置**: mcp/http_server.py

#### fal-ai/sora-2/image-to-video - model
- **问题类型**: invalid_enum
- **描述**: 参数 model 不在 schema 枚举中（已做 int/str 兼容比较）
- **期望**: ['sora-2', 'sora-2-2025-12-08', 'sora-2-2025-10-06']
- **实际**: fal-ai/sora-2/image-to-video
- **位置**: mcp/http_server.py

#### fal-ai/veo3.1/extend-video - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '待延长的视频 URL (720p/1080p, 16:9/9:16)', 'examples': ['https://cdn-video.51sux.com/veo31-examples/veo31_extend_input.mp4']}
- **位置**: mcp/http_server.py

#### fal-ai/veo3.1/first-last-frame-to-video - first_frame_url
- **问题类型**: missing_required
- **描述**: 必填参数 first_frame_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '首帧图片 URL', 'examples': ['https://cdn-video.51sux.com/veo31-examples/veo31-flf2v-input-1.jpeg']}
- **位置**: mcp/http_server.py

#### fal-ai/veo3.1/first-last-frame-to-video - last_frame_url
- **问题类型**: missing_required
- **描述**: 必填参数 last_frame_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '尾帧图片 URL', 'examples': ['https://cdn-video.51sux.com/veo31-examples/veo31-flf2v-input-2.jpeg']}
- **位置**: mcp/http_server.py

#### fal-ai/veo3.1/reference-to-video - image_urls
- **问题类型**: missing_required
- **描述**: 必填参数 image_urls 未在 normalize 结果中或为空
- **期望**: {'type': 'array', 'description': '参考图片 URL 列表，保持主体外观一致', 'examples': [['https://cdn-video.51sux.com/veo31-examples/veo31-r2v-input-1.png', 'https://cdn-video.51sux.com/veo31-examples/veo31-r2v-input-2.png', 'https://cdn-video.51sux.com/veo31-examples/veo31-r2v-input-3.png']]}
- **位置**: mcp/http_server.py

#### fal-ai/veo3.1/fast/extend-video - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '待延长的视频 URL (720p/1080p, 16:9/9:16)', 'examples': ['https://cdn-video.51sux.com/veo31-examples/veo31_extend_input.mp4']}
- **位置**: mcp/http_server.py

#### fal-ai/veo3.1/fast/first-last-frame-to-video - first_frame_url
- **问题类型**: missing_required
- **描述**: 必填参数 first_frame_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '首帧图片 URL', 'examples': ['https://cdn-video.51sux.com/veo31-examples/veo31-flf2v-input-1.jpeg']}
- **位置**: mcp/http_server.py

#### fal-ai/veo3.1/fast/first-last-frame-to-video - last_frame_url
- **问题类型**: missing_required
- **描述**: 必填参数 last_frame_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '尾帧图片 URL', 'examples': ['https://cdn-video.51sux.com/veo31-examples/veo31-flf2v-input-2.jpeg']}
- **位置**: mcp/http_server.py

#### wan/v2.6/reference-to-video - video_urls
- **问题类型**: missing_required
- **描述**: 必填参数 video_urls 未在 normalize 结果中或为空
- **期望**: {'type': 'array', 'description': '参考视频 URL 列表（1-3 个视频）。视频帧率需至少 16 FPS。在提示词中使用 @Video1, @Video2, @Video3 引用', 'examples': [['https://cdn-video.51sux.com/model-examples/20260203/wan-r2v-input1.mp4', 'https://cdn-video.51sux.com/model-examples/20260203/wan-r2v-input2.mp4']]}
- **位置**: mcp/http_server.py

#### fal-ai/bytedance/seedance/v1/lite/reference-to-video - reference_image_urls
- **问题类型**: missing_required
- **描述**: 必填参数 reference_image_urls 未在 normalize 结果中或为空
- **期望**: {'type': 'array', 'description': '参考图片 URL 列表（1-4 张）。参考图中的人物、物体等元素会出现在生成的视频中', 'examples': [['https://cdn-video.51sux.com/seedance-examples/20260204/seedance_reference.jpeg', 'https://cdn-video.51sux.com/seedance-examples/20260204/seedance_reference_2.jpeg']]}
- **位置**: mcp/http_server.py

#### openrouter/router/vision - model
- **问题类型**: invalid_enum
- **描述**: 参数 model 不在 schema 枚举中（已做 int/str 兼容比较）
- **期望**: ['google/gemini-3-pro-preview', 'google/gemini-3-flash-preview', 'google/gemini-2.5-flash', 'anthropic/claude-opus-4.6']
- **实际**: openrouter/router/vision
- **位置**: mcp/http_server.py

#### openrouter/router/video - model
- **问题类型**: invalid_enum
- **描述**: 参数 model 不在 schema 枚举中（已做 int/str 兼容比较）
- **期望**: ['google/gemini-3-pro-preview', 'google/gemini-3-flash-preview', 'google/gemini-2.5-flash', 'anthropic/claude-opus-4.6']
- **实际**: openrouter/router/video
- **位置**: mcp/http_server.py

#### xai/grok-imagine-video/edit-video - video_url
- **问题类型**: missing_required
- **描述**: 必填参数 video_url 未在 normalize 结果中或为空
- **期望**: {'type': 'string', 'description': '输入视频 URL。视频将被缩放至最大 854x480 像素并截断至 8 秒', 'examples': ['https://cdn-video.51sux.com/grok-video-examples/grok_v2v_input_video.mp4']}
- **位置**: mcp/http_server.py
