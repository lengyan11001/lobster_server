# xAI Video Proxy

This is the source-controlled service deployed on the overseas proxy host at
`/opt/xai_video_proxy`. It is intentionally separate from the main Lobster
backend because the proxy host can access the xAI official API and provider
video URLs that may not be reachable from China or an end-user network.

The production service listens on port `19802` and keeps the existing internal
API shape used by `backend/app/api/comfly_proxy.py`.

## Request paths

- `POST /xai/v1/videos/generations`
- `GET /xai/v1/videos/{request_id}`
- `GET /xai/v1/videos/{request_id}/content`
- `POST /media/transfer-to-tos`
- `GET /health`

All `/xai/*` calls require:

```http
Authorization: Bearer <XAI_PROXY_TOKEN>
```

The standalone transfer endpoint requires:

```http
X-Video-Transfer-Token: <VIDEO_TRANSFER_TOKEN>
```

## Grok image-to-video flow

The caller continues to send a normal public image URL:

```json
{
  "model": "grok-imagine-video-1.5-preview",
  "prompt": "Generate a product video",
  "image": {"url": "https://example.com/input.png"},
  "duration": 10
}
```

Internally the proxy:

1. Validates that the URL is public and not a private-network address.
2. Streams the image to a spooled temporary file on the proxy host.
3. Uploads the image to xAI Files using multipart form data.
4. Submits the video request to xAI with `image.file_id`.
5. Remembers the temporary file ID by xAI request ID.
6. Deletes the temporary xAI input file when polling reaches a terminal state.
7. Downloads a completed xAI video on the proxy host and uploads it to TOS.
8. Replaces the returned `video.url` / `video_url` with the public TOS URL.

The external request and response contract does not change for the Lobster
backend or the desktop client.

## Deploy

```bash
cd /opt/xai_video_proxy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# Fill credentials in .env on the host only.
sudo cp xai-video-proxy.service.example /etc/systemd/system/xai-video-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now xai-video-proxy.service
sudo systemctl status xai-video-proxy.service
```

Do not commit `.env`, xAI keys, shared proxy tokens, or TOS credentials.
