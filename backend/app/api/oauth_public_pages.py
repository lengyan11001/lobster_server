"""OAuth 同意屏幕用公开页：应用主页、服务条款（HTTPS、无需登录）。与 privacy_policy 并列供 Google Cloud 填写链接。"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_HOME_ZH = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>应用说明</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #222; }
    h1 { font-size: 1.5rem; }
    p { margin: 0.75rem 0; }
    .lang { margin-bottom: 1rem; font-size: 0.9rem; }
    .lang a { color: #1565c0; }
    nav { margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid #e0e0e0; font-size: 0.95rem; }
    nav a { color: #1565c0; margin-right: 1rem; }
  </style>
</head>
<body>
  <p class="lang"><a href="/oauth-app-home-en">English</a></p>
  <h1>应用说明</h1>
  <p>本页面为面向 Google OAuth 等第三方授权流程的对外说明页，用于说明本服务的基本用途。</p>
  <p>龙虾（Lobster）提供与 AI 能力、素材与发布相关的服务；具体功能以实际产品为准。</p>
  <nav>
    <a href="/privacy-policy">隐私政策</a>
    <a href="/terms-of-service">服务条款</a>
  </nav>
  <p><small>最后更新：2026-03-26</small></p>
</body>
</html>
"""

_HOME_EN = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Application</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; max-width: 42rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; color: #222; }
    h1 { font-size: 1.5rem; }
    p { margin: 0.75rem 0; }
    .lang { margin-bottom: 1rem; font-size: 0.9rem; }
    .lang a { color: #1565c0; }
    nav { margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid #e0e0e0; font-size: 0.95rem; }
    nav a { color: #1565c0; margin-right: 1rem; }
  </style>
</head>
<body>
  <p class="lang"><a href="/oauth-app-home">中文</a></p>
  <h1>Application information</h1>
  <p>This page describes our service for Google OAuth and similar authorization flows.</p>
  <p>Lobster provides AI-related capabilities, assets, and publishing features as applicable.</p>
  <nav>
    <a href="/privacy-policy-en">Privacy Policy</a>
    <a href="/terms-of-service-en">Terms of Service</a>
  </nav>
  <p><small>Last updated: 2026-03-26</small></p>
</body>
</html>
"""

_TERMS_ZH = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>服务条款</title>
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
  <p class="lang"><a href="/terms-of-service-en">English version</a> · <a href="/oauth-app-home">应用说明</a></p>
  <h1>服务条款</h1>
  <p>在使用本服务前，请阅读本条款。继续使用即表示您理解并同意下列内容（以适用法律允许的范围为准）。</p>

  <h2>服务内容与变更</h2>
  <p>我们可能不时调整、暂停或终止部分功能；重大变更将依适用法律或产品内说明处理。</p>

  <h2>用户义务</h2>
  <p>您应合法使用本服务，不得利用本服务从事违法、侵权或滥用第三方平台（含 Google、YouTube 等）政策的行为。</p>

  <h2>第三方服务</h2>
  <p>若您通过 OAuth 等方式连接第三方账号，除受本条款约束外，亦须遵守该第三方的服务条款与政策。</p>

  <h2>责任限制</h2>
  <p>在适用法律允许的最大范围内，我们对因使用或无法使用本服务而产生的间接、附带或后果性损害不承担责任。</p>

  <h2>联系我们</h2>
  <p>如有疑问，请通过产品内公布的联系方式与我们联系。</p>

  <p><small>最后更新日期：2026-03-26</small></p>
</body>
</html>
"""

_TERMS_EN = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Terms of Service</title>
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
  <p class="lang"><a href="/terms-of-service">中文版</a> · <a href="/oauth-app-home-en">Application info</a></p>
  <h1>Terms of Service</h1>
  <p>By using this service, you agree to the following (to the extent permitted by applicable law).</p>

  <h2>Service and changes</h2>
  <p>We may modify, suspend, or discontinue features. Material changes will be handled as required by law or in-product notices.</p>

  <h2>Your responsibilities</h2>
  <p>You must use the service lawfully and comply with third-party policies (including Google/YouTube) when connecting accounts.</p>

  <h2>Third-party services</h2>
  <p>OAuth and similar integrations are also governed by the respective third party’s terms.</p>

  <h2>Limitation of liability</h2>
  <p>To the maximum extent permitted by law, we are not liable for indirect, incidental, or consequential damages.</p>

  <h2>Contact</h2>
  <p>For questions, use the contact method published in the product.</p>

  <p><small>Last updated: 2026-03-26</small></p>
</body>
</html>
"""


@router.get("/oauth-app-home", response_class=HTMLResponse, include_in_schema=False)
def oauth_app_home_zh():
    return HTMLResponse(content=_HOME_ZH, media_type="text/html; charset=utf-8")


@router.get("/oauth-app-home-en", response_class=HTMLResponse, include_in_schema=False)
def oauth_app_home_en():
    return HTMLResponse(content=_HOME_EN, media_type="text/html; charset=utf-8")


@router.get("/terms-of-service", response_class=HTMLResponse, include_in_schema=False)
def terms_of_service_zh():
    return HTMLResponse(content=_TERMS_ZH, media_type="text/html; charset=utf-8")


@router.get("/terms-of-service-en", response_class=HTMLResponse, include_in_schema=False)
def terms_of_service_en():
    return HTMLResponse(content=_TERMS_EN, media_type="text/html; charset=utf-8")
