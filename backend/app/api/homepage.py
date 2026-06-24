from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()
_ROOT = Path(__file__).resolve().parents[3]
_H5_INDEX = _ROOT / "h5_static" / "index.html"


def _asset(path: str) -> str:
    return path


_HOME_HTML = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="dark light" />
  <title>必火AI员工 - 岗位级 AI 执行系统</title>
  <meta name="description" content="必火AI员工是一套面向企业营销、获客、内容生产、素材管理和客户服务的岗位级 AI 执行系统。" />
  <style>
    :root {{
      --bg: #06111f;
      --panel: rgba(10, 24, 44, .72);
      --panel-strong: rgba(12, 31, 58, .9);
      --ink: #edf7ff;
      --muted: #9db2ca;
      --line: rgba(126, 190, 255, .18);
      --blue: #3c7dff;
      --cyan: #20d5ff;
      --green: #38e0b4;
      --gold: #f4c96b;
      --shadow: 0 30px 90px rgba(0, 0, 0, .36);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 8%, rgba(32, 213, 255, .20), transparent 28rem),
        radial-gradient(circle at 85% 5%, rgba(60, 125, 255, .24), transparent 26rem),
        linear-gradient(180deg, #07172b 0%, #06111f 46%, #091322 100%);
      font-family: "Inter", "HarmonyOS Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif;
      letter-spacing: 0;
    }}
    a {{ color: inherit; text-decoration: none; }}
    .shell {{ width: min(1180px, calc(100vw - 40px)); margin: 0 auto; }}
    .nav {{
      position: sticky; top: 0; z-index: 20;
      border-bottom: 1px solid rgba(255,255,255,.08);
      background: rgba(5, 13, 26, .72);
      backdrop-filter: blur(18px);
    }}
    .nav-inner {{ height: 72px; display: flex; align-items: center; justify-content: space-between; gap: 20px; }}
    .brand {{ display: flex; align-items: center; gap: 12px; font-weight: 800; }}
    .brand img {{ width: 38px; height: 38px; border-radius: 12px; box-shadow: 0 0 28px rgba(32,213,255,.35); }}
    .brand span {{ font-size: 18px; }}
    .nav-links {{ display: flex; align-items: center; gap: 22px; color: var(--muted); font-size: 14px; }}
    .nav-links a:hover {{ color: var(--ink); }}
    .btn {{
      display: inline-flex; align-items: center; justify-content: center; gap: 8px;
      min-height: 42px; padding: 0 18px; border-radius: 999px;
      background: linear-gradient(135deg, var(--blue), var(--cyan));
      color: #fff; font-weight: 700; box-shadow: 0 14px 36px rgba(32, 126, 255, .28);
      border: 1px solid rgba(255,255,255,.18);
    }}
    .btn.secondary {{ background: rgba(255,255,255,.07); color: var(--ink); box-shadow: none; }}
    .hero {{ position: relative; min-height: calc(100vh - 72px); padding: 72px 0 54px; overflow: hidden; }}
    .hero-grid {{ display: grid; grid-template-columns: minmax(0, 1.02fr) minmax(420px, .98fr); gap: 42px; align-items: center; }}
    .eyebrow {{
      display: inline-flex; align-items: center; gap: 9px;
      padding: 8px 12px; border: 1px solid var(--line); border-radius: 999px;
      background: rgba(32, 213, 255, .08); color: #b9ecff; font-weight: 700; font-size: 13px;
    }}
    .eyebrow::before {{ content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 16px var(--green); }}
    h1 {{ margin: 22px 0 18px; font-size: clamp(42px, 6vw, 78px); line-height: 1.02; letter-spacing: 0; }}
    .grad {{ background: linear-gradient(135deg, #fff 0%, #cce9ff 38%, #58dcff 76%, #a8ffdf 100%); -webkit-background-clip: text; color: transparent; }}
    .lead {{ max-width: 680px; color: #bfd0e2; font-size: 18px; line-height: 1.8; margin: 0 0 28px; }}
    .hero-actions {{ display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 34px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; max-width: 660px; }}
    .metric {{ padding: 18px; border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,.05); }}
    .metric strong {{ display: block; font-size: 24px; margin-bottom: 4px; }}
    .metric span {{ color: var(--muted); font-size: 13px; }}
    .product-stage {{
      position: relative; min-height: 560px; border: 1px solid rgba(126,190,255,.22); border-radius: 32px;
      background:
        linear-gradient(180deg, rgba(255,255,255,.09), rgba(255,255,255,.03)),
        url('{_asset("/client/miniprogram/openclaw-hero-bg.jpg")}') center/cover;
      box-shadow: var(--shadow); overflow: hidden;
    }}
    .product-stage::after {{
      content: ""; position: absolute; inset: 0;
      background: linear-gradient(180deg, rgba(3, 10, 21, .12), rgba(3, 10, 21, .82));
    }}
    .dashboard {{
      position: absolute; z-index: 2; left: 28px; right: 28px; bottom: 28px;
      border: 1px solid rgba(255,255,255,.16); border-radius: 24px;
      background: rgba(6, 17, 31, .78); backdrop-filter: blur(18px); padding: 18px;
    }}
    .dash-top {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }}
    .dash-top strong {{ font-size: 16px; }}
    .status {{ color: #9ef5d3; font-size: 12px; padding: 5px 9px; border-radius: 999px; background: rgba(56,224,180,.12); }}
    .work-row {{ display: grid; grid-template-columns: 42px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 12px; border-radius: 16px; background: rgba(255,255,255,.06); margin-top: 10px; }}
    .avatar {{ width: 42px; height: 42px; border-radius: 14px; background: rgba(32,213,255,.14); display: grid; place-items: center; overflow: hidden; }}
    .avatar img {{ width: 100%; height: 100%; object-fit: cover; }}
    .work-row b {{ display: block; font-size: 14px; }}
    .work-row small {{ color: var(--muted); }}
    .tag {{ color: #c8eeff; font-size: 12px; border: 1px solid rgba(32,213,255,.28); padding: 5px 8px; border-radius: 999px; }}
    section {{ padding: 82px 0; }}
    .section-head {{ display: flex; align-items: end; justify-content: space-between; gap: 24px; margin-bottom: 28px; }}
    .section-head h2 {{ margin: 0; font-size: clamp(28px, 3.5vw, 44px); }}
    .section-head p {{ max-width: 560px; color: var(--muted); line-height: 1.7; margin: 0; }}
    .cards {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }}
    .card {{
      border: 1px solid var(--line); border-radius: 22px; padding: 22px;
      background: linear-gradient(180deg, rgba(255,255,255,.075), rgba(255,255,255,.035));
      box-shadow: 0 20px 60px rgba(0,0,0,.18);
    }}
    .icon {{ width: 42px; height: 42px; border-radius: 14px; display: grid; place-items: center; margin-bottom: 18px; background: rgba(32,213,255,.12); color: #8ceaff; font-weight: 900; }}
    .card h3 {{ margin: 0 0 10px; font-size: 19px; }}
    .card p {{ margin: 0; color: var(--muted); line-height: 1.65; }}
    .feature-band {{
      border: 1px solid var(--line); border-radius: 30px; overflow: hidden;
      display: grid; grid-template-columns: 1fr 1fr; background: rgba(255,255,255,.04);
    }}
    .feature-media {{ min-height: 430px; background: url('{_asset("/client/miniprogram/home_covers/home-video-factory-live.jpg")}') center/cover; }}
    .feature-copy {{ padding: 42px; }}
    .feature-copy h2 {{ margin: 0 0 18px; font-size: 38px; }}
    .steps {{ display: grid; gap: 14px; margin-top: 24px; }}
    .step {{ display: grid; grid-template-columns: 36px 1fr; gap: 12px; align-items: start; }}
    .step num {{ width: 36px; height: 36px; border-radius: 12px; display: grid; place-items: center; background: rgba(60,125,255,.2); color: #b8d6ff; font-weight: 800; }}
    .step strong {{ display: block; margin-bottom: 4px; }}
    .step span {{ color: var(--muted); line-height: 1.6; }}
    .showcase {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
    .shot {{ min-height: 210px; border: 1px solid var(--line); border-radius: 22px; overflow: hidden; position: relative; background-size: cover; background-position: center; }}
    .shot::after {{ content: ""; position: absolute; inset: 0; background: linear-gradient(180deg, transparent 20%, rgba(2,9,18,.82)); }}
    .shot span {{ position: absolute; z-index: 1; left: 16px; right: 16px; bottom: 16px; font-weight: 800; }}
    .flow {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
    .flow-item {{ border: 1px solid var(--line); border-radius: 18px; padding: 18px; background: rgba(255,255,255,.045); }}
    .flow-item small {{ color: var(--cyan); font-weight: 800; }}
    .flow-item strong {{ display: block; margin: 8px 0; }}
    .flow-item p {{ margin: 0; color: var(--muted); line-height: 1.55; font-size: 14px; }}
    .cta {{ padding-bottom: 96px; }}
    .cta-box {{
      border-radius: 34px; padding: 46px;
      background:
        radial-gradient(circle at 90% 15%, rgba(32,213,255,.24), transparent 24rem),
        linear-gradient(135deg, rgba(60,125,255,.22), rgba(56,224,180,.10));
      border: 1px solid rgba(255,255,255,.16);
      display: flex; align-items: center; justify-content: space-between; gap: 24px;
    }}
    .cta h2 {{ margin: 0 0 10px; font-size: 38px; }}
    .cta p {{ margin: 0; color: var(--muted); }}
    footer {{ padding: 28px 0 42px; border-top: 1px solid rgba(255,255,255,.08); color: var(--muted); }}
    .footer-row {{ display: flex; justify-content: space-between; gap: 20px; flex-wrap: wrap; }}
    .footer-row a {{ color: #c5d9ef; margin-left: 16px; }}
    @media (max-width: 920px) {{
      .nav-links {{ display: none; }}
      .hero-grid, .feature-band {{ grid-template-columns: 1fr; }}
      .product-stage {{ min-height: 500px; }}
      .cards, .flow {{ grid-template-columns: 1fr 1fr; }}
      .showcase {{ grid-template-columns: 1fr 1fr; }}
      .cta-box {{ align-items: flex-start; flex-direction: column; }}
    }}
    @media (max-width: 620px) {{
      .shell {{ width: min(100vw - 24px, 1180px); }}
      .hero {{ padding-top: 46px; }}
      .metrics, .cards, .flow, .showcase {{ grid-template-columns: 1fr; }}
      .product-stage {{ min-height: 460px; border-radius: 24px; }}
      .dashboard {{ left: 14px; right: 14px; bottom: 14px; }}
      .feature-copy, .cta-box {{ padding: 26px; }}
      .section-head {{ display: block; }}
      .section-head p {{ margin-top: 12px; }}
    }}
  </style>
</head>
<body>
  <nav class="nav">
    <div class="shell nav-inner">
      <a class="brand" href="#top">
        <img src="/h5-static/bihu_256.png" alt="必火AI员工" />
        <span>必火AI员工</span>
      </a>
      <div class="nav-links">
        <a href="#roles">AI岗位</a>
        <a href="#content">内容生产</a>
        <a href="#growth">获客增长</a>
        <a href="#workflow">工作流</a>
        <a href="/h5">H5工作台</a>
      </div>
      <a class="btn" href="/h5">立即体验</a>
    </div>
  </nav>

  <main id="top">
    <section class="hero">
      <div class="shell hero-grid">
        <div>
          <div class="eyebrow">岗位级 AI 执行系统</div>
          <h1><span class="grad">把内容、获客、客服和发布</span><br/>交给一组 AI 员工</h1>
          <p class="lead">必火AI员工面向企业日常增长场景，把素材库、技能商店、定时任务、IP日更、公众号排版、视频号文案提取、企业微信客服和移动端协同整合成一套可落地的 AI 工作台。</p>
          <div class="hero-actions">
            <a class="btn" href="/h5">打开 H5 工作台</a>
            <a class="btn secondary" href="#workflow">查看能力地图</a>
          </div>
          <div class="metrics">
            <div class="metric"><strong>10+</strong><span>营销与内容岗位能力</span></div>
            <div class="metric"><strong>7x24</strong><span>定时任务与客服响应</span></div>
            <div class="metric"><strong>多端</strong><span>online / H5 / 小程序协同</span></div>
          </div>
        </div>
        <div class="product-stage" aria-label="AI员工工作台">
          <div class="dashboard">
            <div class="dash-top"><strong>今日 AI 员工运行状态</strong><span class="status">在线协同中</span></div>
            <div class="work-row"><div class="avatar"><img src="/h5-static/h5-employee-male-working.png" alt="" /></div><div><b>市场部 · IP日更文案</b><small>行业热点、同行作品、记忆资料联合生成</small></div><span class="tag">生成中</span></div>
            <div class="work-row"><div class="avatar"><img src="/h5-static/h5-employee-female-working.png" alt="" /></div><div><b>客服部 · 企业微信客服</b><small>接收客户消息，多轮 AI 回复</small></div><span class="tag">监听中</span></div>
            <div class="work-row"><div class="avatar"><img src="/h5-static/h5-boss-avatar.png" alt="" /></div><div><b>运营部 · 定时任务</b><small>按模板下发内容生产和发布动作</small></div><span class="tag">待执行</span></div>
          </div>
        </div>
      </div>
    </section>

    <section id="roles">
      <div class="shell">
        <div class="section-head">
          <h2>不是单个工具，是可分工的 AI 员工</h2>
          <p>围绕企业每天都要做的内容、获客、客服、发布和素材管理，把能力组织成岗位，而不是让用户在一堆模型里选择。</p>
        </div>
        <div class="cards">
          <div class="card"><div class="icon">M</div><h3>市场部</h3><p>IP日更文案、文案+创意图片、公众号文章、视频号口播提取，支撑持续内容生产。</p></div>
          <div class="card"><div class="icon">S</div><h3>销售部</h3><p>抖音获客、线索挖掘、同行数据分析，把平台数据转成跟进名单和沟通素材。</p></div>
          <div class="card"><div class="icon">C</div><h3>客服部</h3><p>企业微信客服和微信助手可接入知识资料，收到消息后进行多轮 AI 回复。</p></div>
          <div class="card"><div class="icon">O</div><h3>运营部</h3><p>定时任务、素材库、生成记录和发布中心，把一次性生成变成可追踪的流程。</p></div>
          <div class="card"><div class="icon">V</div><h3>视频生产</h3><p>爆款TVC、创意分镜头视频、模板视频、速推视频制作，覆盖从图文到成片。</p></div>
          <div class="card"><div class="icon">D</div><h3>数字资产</h3><p>素材入库、生成记录、视频号文案转写、高质量 3D 模型，沉淀可复用资产。</p></div>
        </div>
      </div>
    </section>

    <section id="content">
      <div class="shell feature-band">
        <div class="feature-media"></div>
        <div class="feature-copy">
          <div class="eyebrow">内容生产流水线</div>
          <h2>从记忆资料到可发布内容</h2>
          <p class="lead">AI 会先理解企业资料，再结合行业关键词、同行作品和平台特点，生成口播、朋友圈文案、配图提示、公众号文章与视频脚本。</p>
          <div class="steps">
            <div class="step"><num>1</num><div><strong>配置模板</strong><span>保存关键词、同行账号、记忆文件和不同平台的生成要求。</span></div></div>
            <div class="step"><num>2</num><div><strong>同步数据</strong><span>查询视频号、抖音、行业榜单和同行内容，形成可复用数据源。</span></div></div>
            <div class="step"><num>3</num><div><strong>生成结果</strong><span>文案先入记录，图片和视频由用户按需触发，过程可追踪可复用。</span></div></div>
          </div>
        </div>
      </div>
    </section>

    <section id="growth">
      <div class="shell">
        <div class="section-head">
          <h2>增长场景一次接入</h2>
          <p>围绕企业内容增长，把“资料理解、平台数据、AI生成、素材管理、发布跟进”串起来。</p>
        </div>
        <div class="showcase">
          <div class="shot" style="background-image:url('/client/miniprogram/home_covers/home-image-social.jpg')"><span>朋友圈图文日更</span></div>
          <div class="shot" style="background-image:url('/client/miniprogram/home_covers/home-image-poster.jpg')"><span>海报与创意图片</span></div>
          <div class="shot" style="background-image:url('/client/miniprogram/home_covers/home-video-store-acquisition.jpg')"><span>短视频获客</span></div>
          <div class="shot" style="background-image:url('/client/miniprogram/home_covers/home-image-ecommerce.jpg')"><span>电商详情页与素材</span></div>
        </div>
      </div>
    </section>

    <section id="workflow">
      <div class="shell">
        <div class="section-head">
          <h2>完整闭环：配置、执行、记录、复盘</h2>
          <p>每次任务都有状态和结果，不再把 AI 生成当作一次性聊天。</p>
        </div>
        <div class="flow">
          <div class="flow-item"><small>01</small><strong>资料沉淀</strong><p>上传客服资料、企业介绍、产品卖点和案例，让 AI 持续理解业务。</p></div>
          <div class="flow-item"><small>02</small><strong>模板下发</strong><p>管理后台或 online 配置多套模板，面向不同用户和场景复用。</p></div>
          <div class="flow-item"><small>03</small><strong>自动执行</strong><p>定时任务在本机 online 执行，H5 和小程序可查看进度与结果。</p></div>
          <div class="flow-item"><small>04</small><strong>资产回流</strong><p>生成图片、视频、文案和链接进入素材库与生成记录，便于管理审计。</p></div>
        </div>
      </div>
    </section>

    <section class="cta">
      <div class="shell cta-box">
        <div>
          <h2>让 AI 真正进入岗位，而不是停在聊天框里</h2>
          <p>从内容生产到客户服务，从移动端到桌面端，必火AI员工把日常任务变成可执行、可追踪、可复盘的流程。</p>
        </div>
        <a class="btn" href="/h5">进入 H5 工作台</a>
      </div>
    </section>
  </main>

  <footer>
    <div class="shell footer-row">
      <span>© 2026 必火AI员工 · 岗位级 AI 执行系统</span>
      <span>
        <a href="/privacy-policy">隐私政策</a>
        <a href="/terms-of-service">服务条款</a>
        <a href="/admin">管理后台</a>
      </span>
    </div>
  </footer>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def homepage(request: Request):
    host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    if host == "h5.bhzn.top" or host.startswith("h5."):
        if not _H5_INDEX.is_file():
            raise HTTPException(status_code=404, detail="H5 页面未打包")
        return FileResponse(str(_H5_INDEX))
    return HTMLResponse(content=_HOME_HTML, media_type="text/html; charset=utf-8")
