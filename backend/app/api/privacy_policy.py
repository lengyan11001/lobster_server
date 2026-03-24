"""公开隐私政策页（Meta 应用审核等要求可访问的 HTTPS URL，无需登录）。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>隐私政策</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #222; }
    h1 { font-size: 1.5rem; }
    h2 { font-size: 1.1rem; margin-top: 1.5rem; }
    p, li { margin: 0.5rem 0; }
    .lang { margin-bottom: 1rem; font-size: 0.9rem; }
    .lang a { color: #1565c0; }
  </style>
</head>
<body>
  <p class="lang"><a href="/privacy-policy-en">English version</a></p>
  <h1>隐私政策</h1>
  <p>本政策说明我们在提供与 Facebook / Meta 平台相关的功能（例如通过 Messenger 与公共主页访客沟通）时，如何处理个人信息。</p>

  <h2>我们收集的信息</h2>
  <p>我们可能收集或处理如下信息：</p>
  <ul>
    <li>您在使用本服务时主动提供的信息（例如账号、联系方式等，以实际产品功能为准）；</li>
    <li>通过 Meta 平台接口获得的、与对话或公共主页相关的内容（例如消息内容、用户标识等，以 Meta 向您授权的范围为准）；</li>
    <li>为提供服务所必需的技术信息（例如服务器日志、请求时间等）。</li>
  </ul>

  <h2>我们如何使用这些信息</h2>
  <p>我们仅在提供、维护、改进本服务及履行法律义务所必需的范围内使用上述信息，例如：处理客服对话、保障服务安全、故障排查与合规审计。</p>

  <h2>数据删除请求</h2>
  <p>若您希望删除由我们处理的与您相关的个人信息，请通过本服务对外公布的联系方式或应用内说明与我们联系。我们将在适用法律规定的期限内处理您的请求。</p>

  <h2>政策更新</h2>
  <p>我们可能不时更新本政策。更新后的版本将发布于本页面，并自发布之日起生效。</p>

  <p><small>最后更新日期：2026-03-24</small></p>
</body>
</html>
"""

_HTML_EN = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Privacy Policy</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #222; }
    h1 { font-size: 1.5rem; }
    h2 { font-size: 1.1rem; margin-top: 1.5rem; }
    p, li { margin: 0.5rem 0; }
    .lang { margin-bottom: 1rem; font-size: 0.9rem; }
    .lang a { color: #1565c0; }
  </style>
</head>
<body>
  <p class="lang"><a href="/privacy-policy">中文版</a></p>
  <h1>Privacy Policy</h1>
  <p>This policy describes how we process personal information when providing features related to the Facebook / Meta platform (for example, communicating with visitors to your Facebook Page via Messenger).</p>

  <h2>Information we collect</h2>
  <p>We may collect or process:</p>
  <ul>
    <li>Information you provide when using our service (such as account or contact details, depending on product features);</li>
    <li>Content obtained through Meta platform APIs related to conversations or your Page (such as message content and user identifiers), only within the scope Meta authorizes;</li>
    <li>Technical information necessary to operate the service (such as server logs and request timestamps).</li>
  </ul>

  <h2>How we use this information</h2>
  <p>We use the above information only as needed to provide, maintain, and improve our service and to meet legal obligations—for example, handling support conversations, securing the service, troubleshooting, and compliance.</p>

  <h2>Requests to delete data</h2>
  <p>If you wish to delete personal information we process about you, contact us using the contact method published for this service or in-app instructions. We will respond within the timeframes required by applicable law.</p>

  <h2>Changes to this policy</h2>
  <p>We may update this policy from time to time. The updated version will be posted on this page and is effective from the date of publication.</p>

  <p><small>Last updated: 2026-03-24</small></p>
</body>
</html>
"""


@router.get("/privacy-policy", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy_html():
    return HTMLResponse(content=_HTML, media_type="text/html; charset=utf-8")


@router.get("/privacy-policy-en", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy_html_en():
    return HTMLResponse(content=_HTML_EN, media_type="text/html; charset=utf-8")
