/* shop 独立站前端公共逻辑（P2：样张 → 读真实接口的正式前端）
 * 数据源：GET /api/shop/store/{slug} · GET /api/shop/products/{id}
 * 归因：  POST /api/shop/referral/click（链接参数 ?r=<promoter_user_id>&rf=<code>，兼容别名 sref / uid）
 * 下单：  POST /api/shop/orders
 * 纯前端文件：不改后端接口、不动生产配置。
 */
(function () {
  'use strict';

  var REF_KEY = 'shop_ref_v1';
  var VISITOR_KEY = 'shop_visitor_v1';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function attr(v) { return esc(v == null ? '' : v); }
  function yuan(cents) {
    var v = Number(cents || 0) / 100;
    return '¥' + (v % 1 === 0 ? String(v) : v.toFixed(2));
  }
  function bpText(bp) {
    var n = Number(bp || 0) / 100;
    return (n % 1 === 0 ? String(n) : n.toFixed(1)) + '%';
  }

  // ── 归因：会话内保存推广参数，站内跳转全部带上 ──
  function loadRef() {
    try { return JSON.parse(sessionStorage.getItem(REF_KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function readRef() {
    var q = new URLSearchParams(location.search);
    var code = (q.get('rf') || q.get('sref') || '').trim();
    var promoter = parseInt(q.get('r') || q.get('uid') || '0', 10) || 0;
    var cur = loadRef();
    if (code || promoter) {
      var merged = { code: code || cur.code || '', promoter: promoter || cur.promoter || 0, at: Date.now() };
      try { sessionStorage.setItem(REF_KEY, JSON.stringify(merged)); } catch (e) { /* 隐私模式忽略 */ }
      return merged;
    }
    return cur;
  }
  function withRef(url) {
    var ref = loadRef();
    if (!ref.code && !ref.promoter) return url;
    var u = new URL(url, location.origin);
    if (ref.promoter) u.searchParams.set('r', String(ref.promoter));
    if (ref.code) u.searchParams.set('rf', String(ref.code));
    return u.pathname + u.search;
  }
  function visitorId() {
    try {
      var v = localStorage.getItem(VISITOR_KEY);
      if (!v) {
        v = 'v' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
        localStorage.setItem(VISITOR_KEY, v);
      }
      return v;
    } catch (e) { return ''; }
  }
  function api(path, opts) {
    var init = opts || {};
    init.headers = Object.assign({ 'Content-Type': 'application/json' }, init.headers || {});
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok || data.ok === false) {
          var msg = (data && (data.detail || data.message)) || ('HTTP ' + r.status);
          throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
        }
        return data;
      });
    });
  }
  function trackClick(productId) {
    var ref = loadRef();
    if (!ref.code && !ref.promoter) return Promise.resolve(null);
    var payload = {
      product_id: Number(productId || 0),
      promoter_user_id: ref.promoter || 0,
      code: ref.code || '',
      visitor_id: visitorId(),
      landing_url: location.href
    };
    return api('/api/shop/referral/click', { method: 'POST', body: JSON.stringify(payload) })
      .catch(function (e) { document.body.dataset.clickError = e.message; return null; });
  }

  // ── 渲染小件 ──
  function applyTheme(css, theme) {
    if (css) {
      var el = $('#theme-vars');
      if (!el) { el = document.createElement('style'); el.id = 'theme-vars'; document.head.appendChild(el); }
      el.textContent = ':root{' + css + '}';
    }
    if (theme && theme.heading_font === 'serif') document.documentElement.setAttribute('data-heading', 'serif');
  }
  function emptyState(title, detail) {
    return '<div class="empty"><b>' + esc(title) + '</b><div class="muted">' + esc(detail || '') + '</div></div>';
  }
  function placeholder(label, kind, cls) {
    return '<div class="ph ' + (cls || '') + '"><span>' + esc(label || '商品主图位') + '</span><span>' + esc(kind || 'cover_url') + '</span></div>';
  }
  function coverOf(p) {
    var gallery = (p.media && p.media.gallery) || [];
    var url = p.cover_url || gallery[0] || '';
    if (!url) return placeholder('商品主图位', 'cover_url');
    return '<img class="cover" src="' + attr(url) + '" alt="' + attr(p.title) + '" loading="lazy">';
  }
  function priceLine(p, big) {
    var market = Number(p.market_price_cents || 0) > Number(p.price_cents || 0)
      ? ' <s>' + yuan(p.market_price_cents) + '</s>' : '';
    return '<div class="price' + (big ? ' big' : '') + '">' + yuan(p.price_cents) + market + '</div>';
  }
  function commissionLine(p) {
    if (!Number(p.commission_bp || 0) && !Number(p.commission_estimate_cents || 0)) return '';
    var parts = ['预计佣金 ' + yuan(p.commission_estimate_cents)];
    if (Number(p.commission_bp || 0)) parts.push(bpText(p.commission_bp));
    return '<div class="comm">' + esc(parts.join(' · ')) + '</div>';
  }
  function chips(list) {
    if (!list || !list.length) return '';
    return '<div class="chips">' + list.map(function (t) { return '<span class="chip">' + esc(t) + '</span>'; }).join('') + '</div>';
  }
  function cardHtml(p) {
    return [
      '<a class="card" href="' + attr(withRef('/p/' + p.id)) + '" data-product="' + attr(p.id) + '">',
      coverOf(p),
      '<div class="body">',
      '<div class="name">' + esc(p.title) + '</div>',
      p.subtitle ? '<div class="desc">' + esc(p.subtitle) + '</div>' : '',
      priceLine(p),
      commissionLine(p),
      '</div></a>'
    ].join('');
  }
  function specsTable(specs) {
    if (!specs) return '';
    if (Array.isArray(specs)) return chips(specs.map(function (s) { return typeof s === 'string' ? s : (s.name || s.label || ''); }));
    var keys = Object.keys(specs);
    if (!keys.length) return '';
    return '<table class="specs"><tbody>' + keys.map(function (k) {
      var v = specs[k];
      if (Array.isArray(v)) v = v.join(' / ');
      else if (v && typeof v === 'object') v = JSON.stringify(v);
      return '<tr><th>' + esc(k) + '</th><td>' + esc(v) + '</td></tr>';
    }).join('') + '</tbody></table>';
  }
  function cleanHtml(html) {
    return String(html || '')
      .replace(/<\s*script[\s\S]*?<\s*\/\s*script\s*>/gi, '')
      .replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
      .replace(/javascript:/gi, '');
  }

  // ── 店铺首页 ──
  function renderStore(root, slug) {
    root.innerHTML = '<div class="loading">正在加载店铺…</div>';
    if (!slug) {
      root.innerHTML = emptyState('缺少店铺标识', '请在链接后补上 ?slug=<店铺标识>，例如 /?slug=longshang-fresh');
      document.body.dataset.storeError = 'missing_slug';
      return Promise.resolve();
    }
    return api('/api/shop/store/' + encodeURIComponent(slug)).then(function (data) {
      var store = data.store || {};
      var items = data.items || [];
      var theme = data.theme || {};
      applyTheme(data.theme_css, theme);
      document.title = (store.company_name || '独立站') + ' · 独立商城';
      document.body.dataset.slug = store.slug || slug;

      var intro = String(store.intro || '').split(/\n+/).filter(Boolean);
      var hero = [
        '<section class="hero"><div class="art"></div><div class="inner">',
        '<div class="kicker">INDEPENDENT STORE</div>',
        '<h1>' + esc(store.company_name || '独立商城') + '</h1>',
        intro.length ? '<p class="lede">' + esc(intro[0]) + '</p>' : '<p class="lede">商家直发 · 平台担保 · 推广者按单计佣</p>',
        '<div class="cta"><a class="btn" href="#goods">立即选购</a><a class="btn line" href="#story">了解店铺</a></div>',
        '</div>',
        store.banner_url ? '<img class="banner" src="' + attr(store.banner_url) + '" alt="">' : '',
        '</section>'
      ].join('');

      var hot = items.slice(0, 3);
      var hotSection = hot.length ? [
        '<section id="hot"><div class="title"><h2>热销精选</h2><span>Hot · ' + hot.length + ' items</span></div>',
        '<div class="grid">' + hot.map(cardHtml).join('') + '</div></section>'
      ].join('') : '';

      var story = [
        '<section id="story"><div class="title"><h2>店铺介绍</h2><span>Why us</span></div>',
        '<div class="story"><div class="art"></div><div>',
        intro.length
          ? intro.map(function (t) { return '<p>' + esc(t) + '</p>'; }).join('')
          : '<p>本店商品由 ' + esc(store.company_name || '商家') + ' 直发，价格与佣金以商家后台配置为准。</p>',
        '<p class="gold">本店由 shop.bhzn.top 提供独立站能力，主题风格：' + esc(theme.label || theme.preset || '默认') + '。</p>',
        '</div></div></section>'
      ].join('');

      var trust = [
        '<div class="trust">',
        '<div><b>平台担保</b>订单由 shop.bhzn.top 承接</div>',
        '<div><b>7 天无理由</b>不影响二次销售可退</div>',
        '<div><b>到付 / 线下</b>当前下单通道，商家联系发货</div>',
        '<div><b>佣金透明</b>推广者按订单实付计佣</div>',
        '</div>'
      ].join('');

      var all = [
        '<section id="goods"><div class="title"><h2>全部商品</h2><span>All · ' + items.length + ' items</span></div>',
        items.length ? '<div class="grid">' + items.map(cardHtml).join('') + '</div>' : '<div class="empty"><b>该店铺暂无上架商品</b></div>',
        trust,
        '</section>'
      ].join('');

      root.innerHTML = hero + hotSection + story + all;
      document.body.dataset.storeReady = JSON.stringify({
        slug: store.slug || slug,
        company_name: store.company_name || '',
        preset: theme.preset || '',
        items: items.length,
        first_title: items.length ? items[0].title : '',
        first_href: items.length ? withRef('/p/' + items[0].id) : ''
      });
      if (loadRef().code) trackClick(items.length ? items[0].id : 0);
    }).catch(function (e) {
      root.innerHTML = emptyState('店铺加载失败', e.message);
      document.body.dataset.storeError = e.message;
    });
  }

  // ── 商品详情页 ──
  function renderProduct(root, id) {
    root.innerHTML = '<div class="loading">正在加载商品…</div>';
    var box = { p: null, m: null };
    return api('/api/shop/products/' + encodeURIComponent(id)).then(function (data) {
      box.p = data.product || {};
      box.m = box.p.merchant || {};
      document.title = (box.p.title || '商品详情');
      return trackClick(box.p.id);
    }).then(function () {
      var p = box.p, m = box.m;
      var gallery = ((p.media && p.media.gallery) || []).filter(Boolean);
      var stock = Number(p.stock || 0);
      var shareUrl = p.buy_url || (location.origin + '/p/' + p.id);
      var storeHref = m.slug ? withRef('/?slug=' + encodeURIComponent(m.slug)) : '';
      var detail = String(p.detail_html || '');
      var meta = [
        p.brand ? '品牌 ' + esc(p.brand) : '',
        p.category ? '类目 ' + esc(p.category) : '',
        '销量 ' + Number(p.sales || 0),
        '库存 ' + stock
      ].filter(Boolean).join(' · ');

      root.innerHTML = [
        '<nav class="crumb">',
        storeHref ? '<a href="' + attr(storeHref) + '">' + esc(m.company_name || p.title || '店铺') + '</a>' : esc(m.company_name || '独立商城'),
        ' / <span>' + esc(p.title || '') + '</span></nav>',
        '<div class="pwrap">',
        '<div class="pmedia">' + coverOf(p),
        gallery.length > 1 ? '<div class="thumbs">' + gallery.slice(0, 6).map(function (u) { return '<img src="' + attr(u) + '" alt="" loading="lazy">'; }).join('') + '</div>' : '',
        (p.media && p.media.video) ? '<div class="ph"><span>商品视频位</span><span>' + attr(p.media.video) + '</span></div>' : '',
        '</div>',
        '<div class="pinfo">',
        '<h1>' + esc(p.title || '') + '</h1>',
        p.subtitle ? '<p class="sub">' + esc(p.subtitle) + '</p>' : '',
        chips(p.tags),
        priceLine(p, true),
        '<div class="meta">' + meta + '</div>',
        commissionLine(p),
        m.company_name ? '<div class="merchant">商家 ' + esc(m.company_name) + (storeHref ? ' · <a href="' + attr(storeHref) + '">进店逛逛</a>' : '') + '</div>' : '',
        '<form id="order-form" class="order">',
        '<div class="row"><label for="qty">数量</label><input id="qty" type="number" min="1" max="' + Math.max(1, stock) + '" value="1"></div>',
        '<div class="row"><label for="buyer_name">收货人</label><input id="buyer_name" maxlength="64" placeholder="姓名"></div>',
        '<div class="row"><label for="buyer_phone">手机号</label><input id="buyer_phone" maxlength="32" placeholder="用于联系发货"></div>',
        '<div class="row"><label for="buyer_address">收货地址</label><textarea id="buyer_address" rows="2" maxlength="300" placeholder="省 / 市 / 区 + 详细地址"></textarea></div>',
        '<div class="row"><label for="buyer_remark">备注</label><input id="buyer_remark" maxlength="300" placeholder="选填"></div>',
        '<button class="btn" id="submit-order" type="submit"' + (stock <= 0 ? ' disabled' : '') + '>' + (stock <= 0 ? '暂时缺货' : '立即下单') + '</button>',
        '<div class="tip" id="order-tip">当前为到付 / 线下下单通道，提交后商家会联系你确认发货。</div>',
        '</form>',
        '<div class="share"><button class="btn line" id="copy-link" type="button">复制商品链接</button><span class="muted" id="share-url">' + esc(shareUrl) + '</span></div>',
        '</div></div>',
        '<section class="detail"><div class="title"><h2>商品详情</h2><span>Detail</span></div>',
        (p.specs && Object.keys(p.specs).length) ? '<h3>规格参数</h3>' + specsTable(p.specs) : '',
        detail ? '<div class="detail-html">' + cleanHtml(detail) + '</div>'
          : '<div class="grid2"><div class="ph tall"><span>商品详情长图位</span><span>detail_html</span></div><div class="ph tall"><span>产地 / 工艺实拍位</span><span>gallery</span></div></div>',
        '</section>'
      ].join('');

      Array.prototype.forEach.call(document.querySelectorAll('[data-store-link]'), function (el) {
        if (storeHref) el.setAttribute('href', storeHref + el.getAttribute('href').replace(/^\/?(?=#)/, '/'));
      });
      var brandEl = document.querySelector('header .brand');
      if (brandEl && storeHref) brandEl.setAttribute('href', storeHref);      document.body.dataset.productReady = JSON.stringify({
        id: p.id, title: p.title || '', price_cents: Number(p.price_cents || 0),
        commission_bp: Number(p.commission_bp || 0), stock: stock
      });

      var copyBtn = $('#copy-link', root);
      if (copyBtn) {
        copyBtn.addEventListener('click', function () {
          if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(shareUrl).catch(function () {});
          copyBtn.textContent = '已复制';
          setTimeout(function () { copyBtn.textContent = '复制商品链接'; }, 1500);
        });
      }
      var form = $('#order-form', root);
      if (form) {
        form.addEventListener('submit', function (ev) {
          ev.preventDefault();
          var btn = $('#submit-order', root);
          var tip = $('#order-tip', root);
          var ref = loadRef();
          var body = {
            product_id: Number(p.id),
            qty: Math.max(1, parseInt((($('#qty', root) || {}).value || '1'), 10) || 1),
            buyer_name: (($('#buyer_name', root) || {}).value || '').trim(),
            buyer_phone: (($('#buyer_phone', root) || {}).value || '').trim(),
            buyer_address: (($('#buyer_address', root) || {}).value || '').trim(),
            remark: (($('#buyer_remark', root) || {}).value || '').trim(),
            referral_code: ref.code || '',
            promoter_user_id: ref.promoter || 0,
            shipping_cents: 0
          };
          btn.disabled = true;
          tip.className = 'tip';
          tip.textContent = '正在提交订单…';
          api('/api/shop/orders', { method: 'POST', body: JSON.stringify(body) }).then(function (res) {
            document.body.dataset.lastOrder = JSON.stringify(res);
            tip.className = 'tip ok';
            tip.innerHTML = '下单成功 · 订单号 <b>' + esc(res.order_no) + '</b> · 应付 ' + yuan(res.pay_amount_cents)
              + (Number(res.commission_cents || 0) > 0 ? ' · 本单佣金 ' + yuan(res.commission_cents) : '');
            btn.disabled = false;
          }).catch(function (e) {
            document.body.dataset.lastOrderError = e.message;
            tip.className = 'tip err';
            tip.textContent = '下单失败：' + e.message + (/authenticat|401|凭证/i.test(e.message) ? ' —— 该接口要求登录态，请先登录后重试。' : '');
            btn.disabled = false;
          });
        });
      }
    }).catch(function (e) {
      root.innerHTML = emptyState('商品加载失败', e.message);
      document.body.dataset.productError = e.message;
    });
  }

  var booted = false;
  function boot() {
    if (booted) return;
    booted = true;
    var root = $('#app') || document.body;
    readRef();
    var m = location.pathname.match(/^\/p\/(\d+)/);
    var q = new URLSearchParams(location.search);
    if (m) { renderProduct(root, m[1]); return; }
    var id = (q.get('product_id') || q.get('id') || '').trim();
    if (/^\d+$/.test(id)) { renderProduct(root, id); return; }
    renderStore(root, (q.get('slug') || document.body.dataset.slug || '').trim());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  window.ShopFront = { renderStore: renderStore, renderProduct: renderProduct, ref: loadRef, trackClick: trackClick, yuan: yuan, esc: esc, attr: attr, api: api, coverOf: coverOf, priceLine: priceLine, commissionLine: commissionLine, withRef: withRef, visitorId: visitorId };
})();
/* ───────────── P3 选品广场：GET /api/shop/plaza + POST /api/shop/plaza/link ───────────── */
(function () {
  'use strict';
  var F = window.ShopFront;
  if (!F || !document.body.hasAttribute('data-plaza')) return;
  var TOKEN_KEY = 'shop_token_v1';

  function token() {
    var q = new URLSearchParams(location.search);
    var t = (q.get('token') || '').trim();
    if (t) { try { sessionStorage.setItem(TOKEN_KEY, t); } catch (e) { /* 隐私模式 */ } return t; }
    try {
      return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem('lobster_token') || localStorage.getItem('token') || '';
    } catch (e) { return ''; }
  }
  function authed(path, opts) {
    var init = opts || {};
    init.headers = { 'Content-Type': 'application/json' };
    if (token()) init.headers.Authorization = 'Bearer ' + token();
    return fetch(path, init).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || d.ok === false) {
          var m = (d && (d.detail || d.message)) || ('HTTP ' + r.status);
          var err = new Error(typeof m === 'string' ? m : JSON.stringify(m));
          err.status = r.status;
          throw err;
        }
        return d;
      });
    });
  }
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return Promise.reject(new Error('clipboard unavailable'));
  }
  var state = { keyword: '', category: '', sort: 'heat', page: 1, size: 24 };

  function plazaCard(p) {
    return [
      '<div class="card plaza" data-product="' + F.esc(p.id) + '">',
      F.coverOf(p),
      '<div class="body">',
      '<div class="name">' + F.esc(p.title) + '</div>',
      p.subtitle ? '<div class="desc">' + F.esc(p.subtitle) + '</div>' : '',
      F.priceLine(p),
      F.commissionLine(p),
      p.merchant ? '<div class="desc">' + F.esc(p.merchant.company_name || '') + (p.category ? ' · ' + F.esc(p.category) : '') + '</div>' : '',
      '<div class="acts">',
      '<button class="btn copy" type="button" data-copy="' + F.esc(p.id) + '">一键复制推广链接</button>',
      '<a class="btn line" href="' + F.attr(F.withRef('/p/' + p.id)) + '">预览商品页</a>',
      '</div>',
      '<div class="tip" data-tip="' + F.esc(p.id) + '"></div>',
      '</div></div>'
    ].join('');
  }

  function bindCopy(root) {
    Array.prototype.forEach.call(root.querySelectorAll('[data-copy]'), function (btn) {
      btn.addEventListener('click', function () {
        var pid = btn.getAttribute('data-copy');
        var tip = root.querySelector('[data-tip="' + pid + '"]');
        var plain = location.origin + '/p/' + pid;
        btn.disabled = true;
        tip.className = 'tip';
        tip.textContent = '正在生成推广链接…';
        authed('/api/shop/plaza/link', { method: 'POST', body: JSON.stringify({ product_id: Number(pid), source: 'online_plaza' }) })
          .then(function (r) {
            copyText(r.link).catch(function () {});
            document.body.dataset.lastLink = JSON.stringify(r);
            tip.className = 'tip ok';
            tip.innerHTML = '已复制推广链接 · 预计佣金 ' + F.yuan(r.commission_estimate_cents)
              + ' (' + (Number(r.commission_bp || 0) / 100) + '%)<br><span class="muted">' + F.esc(r.link) + '</span>';
            btn.textContent = '已复制';
            btn.disabled = false;
          })
          .catch(function (e) {
            document.body.dataset.lastLinkError = e.message + '#' + (e.status || 0);
            copyText(plain).catch(function () {});
            tip.className = 'tip err';
            tip.textContent = (e.status === 401 || /authenticat|凭证/i.test(e.message))
              ? '需要登录态：已复制普通商品链接 ' + plain + '；登录后可生成带归因 (?r=&rf=) 的推广链接。'
              : '生成失败：' + e.message + '（已复制普通商品链接 ' + plain + '）';
            btn.disabled = false;
          });
      });
    });
  }

  function load(root) {
    var url = '/api/shop/plaza?sort=' + encodeURIComponent(state.sort) + '&page=' + state.page + '&size=' + state.size
      + (state.keyword ? '&keyword=' + encodeURIComponent(state.keyword) : '')
      + (state.category ? '&category=' + encodeURIComponent(state.category) : '');
    root.innerHTML = '<div class="loading">正在加载选品广场…</div>';
    return F.api(url).then(function (d) {
      var items = d.items || [];
      var seen = {}, cats = [];
      items.forEach(function (p) { if (p.category && !seen[p.category]) { seen[p.category] = 1; cats.push(p.category); } });
      var catEl = document.getElementById('cat');
      if (catEl) {
        catEl.innerHTML = '<option value="">全部类目</option>' + cats.map(function (c) {
          return '<option value="' + F.attr(c) + '"' + (c === state.category ? ' selected' : '') + '>' + F.esc(c) + '</option>';
        }).join('');
      }
      root.innerHTML = '<div class="ptitle"><h2>选品广场</h2><span>在售 ' + Number(d.total || 0) + ' 个商品</span></div>'
        + (items.length ? '<div class="grid">' + items.map(plazaCard).join('') + '</div>'
          : '<div class="empty"><b>没有匹配的商品</b><div class="muted">换个关键词或类目再试</div></div>');
      document.body.dataset.plazaReady = JSON.stringify({ total: Number(d.total || 0), items: items.length, cats: cats });
      bindCopy(root);
    }).catch(function (e) {
      root.innerHTML = '<div class="empty"><b>选品广场加载失败</b><div class="muted">' + F.esc(e.message) + '</div></div>';
      document.body.dataset.plazaError = e.message;
    });
  }

  var form = document.getElementById('plaza-filter');
  if (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      state.keyword = (document.getElementById('q').value || '').trim();
      state.category = document.getElementById('cat').value || '';
      state.sort = document.getElementById('sort').value || 'heat';
      state.page = 1;
      document.body.dataset.plazaQuery = JSON.stringify(state);
      load(document.getElementById('app'));
    });
  }
  var box = document.getElementById('auth-box');
  if (box) {
    if (token()) {
      box.innerHTML = '<div class="login-inline"><span class="muted">已登录：可一键生成带归因 (?r=&rf=) 的推广链接</span><button class="btn mini line" id="plaza-logout" type="button">退出登录</button></div>';
      var lo = document.getElementById('plaza-logout');
      if (lo) lo.addEventListener('click', function () {
        try { localStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(TOKEN_KEY); } catch (e) {}
        location.reload();
      });
    } else {
      box.innerHTML = [
        '<div class="login-inline">',
        '<input id="plaza-acct" autocomplete="username" placeholder="手机号 / 邮箱">',
        '<input id="plaza-pwd" type="password" autocomplete="current-password" placeholder="登录密码">',
        '<button class="btn mini" id="plaza-login" type="button">登录</button>',
        '<span class="tip" id="plaza-login-tip">登录后一键复制带归因的推广链接；不登录也能看商品、复制普通链接</span>',
        '</div>'
      ].join('');
      var pbtn = document.getElementById('plaza-login');
      if (pbtn) pbtn.addEventListener('click', function () {
        var acct = (document.getElementById('plaza-acct').value || '').trim();
        var pwd = document.getElementById('plaza-pwd').value || '';
        var tip = document.getElementById('plaza-login-tip');
        if (!acct || !pwd) { tip.className = 'tip err'; tip.textContent = '请输入账号和密码'; return; }
        pbtn.disabled = true; tip.className = 'tip'; tip.textContent = '登录中…';
        fetch('/auth/login-phone-password', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ account: acct, password: pwd }) })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { s: r.status, d: d }; }); })
          .then(function (res) {
            if (res.s !== 200 || !res.d.access_token) throw new Error(res.d.detail || ('HTTP ' + res.s));
            try { localStorage.setItem(TOKEN_KEY, res.d.access_token); } catch (e) {}
            document.body.dataset.plazaLogin = 'ok';
            location.reload();
          })
          .catch(function (e) {
            pbtn.disabled = false; tip.className = 'tip err'; tip.textContent = '登录失败：' + e.message;
            document.body.dataset.plazaLogin = 'fail:' + e.message;
          });
      });
    }
  }  var badge = document.getElementById('auth-state');
  if (badge) badge.textContent = token() ? '已带登录态：可一键生成带归因的推广链接' : '未登录：可在此页登录，或复制普通商品链接';
  document.body.dataset.hasToken = token() ? '1' : '0';
  load(document.getElementById('app'));
})();