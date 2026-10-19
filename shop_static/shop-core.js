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
  // 独立站商品详情：规格选择 / 主图切换（内联调用，避免改原有事件绑定）
  window.__yuan = window.__yuan || function (cents) { return (Number(cents || 0) / 100).toFixed(2); };
  window.__pickSku = function (btn, priceCents) {
    var row = btn.parentNode;
    Array.prototype.forEach.call(row.querySelectorAll('.sku'), function (x) { x.classList.remove('on'); });
    btn.classList.add('on');
    var box = btn.closest('.pinfo') || document;
    var price = box.querySelector('.price');
    if (Number(priceCents) > 0 && price) { price.innerHTML = '\uffe5' + window.__yuan(priceCents); }
  };
  window.__pickThumbImg = function (img) {
    var media = img.closest('.pmedia');
    var main = media && media.querySelector('.cover');
    if (main) main.setAttribute('src', img.getAttribute('src'));
    var thumb = img.closest('.plaza-thumb');
    var wrap = thumb && thumb.parentNode;
    if (wrap) {
      Array.prototype.forEach.call(wrap.querySelectorAll('.plaza-thumb'), function (item) {
        item.classList.toggle('on', item === thumb);
      });
    }
  };

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
    var params = new URLSearchParams(location.search);
    var state = {
      keyword: (params.get('keyword') || '').trim(),
      category: (params.get('category') || '').trim(),
      sort: (params.get('sort') || 'heat').trim(),
      page: 1,
      size: 60
    };
    var currentSlug = slug;

    function syncUrl() {
      try {
        var p = new URLSearchParams();
        p.set('slug', currentSlug);
        if (state.keyword) p.set('keyword', state.keyword);
        if (state.category) p.set('category', state.category);
        if (state.sort && state.sort !== 'heat') p.set('sort', state.sort);
        history.replaceState(null, '', location.pathname + '?' + p.toString());
      } catch (e) { /* 忽略 */ }
    }

    function filterBar(categories) {
      var catOptions = ['<option value="">全部类目</option>'].concat(categories.map(function (c) {
        return '<option value="' + attr(c) + '"' + (c === state.category ? ' selected' : '') + '>' + esc(c) + '</option>';
      })).join('');
      var sortOptions = [['heat', '按热度'], ['new', '按最新'], ['price_asc', '价格从低到高'], ['price_desc', '价格从高到低']].map(function (it) {
        return '<option value="' + it[0] + '"' + (state.sort === it[0] ? ' selected' : '') + '>' + it[1] + '</option>';
      }).join('');
      var dirty = !!(state.keyword || state.category || state.sort !== 'heat');
      return '<form id="store-filter" class="filters" data-store-filter="1">' +
        '<input id="store-q" placeholder="搜商品名 / 关键词，回车或点筛选" value="' + attr(state.keyword) + '">' +
        '<select id="store-cat">' + catOptions + '</select>' +
        '<select id="store-sort">' + sortOptions + '</select>' +
        '<button class="btn" type="submit">筛选</button>' +
        (dirty ? '<button class="btn line" type="button" id="store-reset">重置</button>' : '') +
        '</form>';
    }

    function load(scrollToGoods) {
      var url = '/api/shop/store/' + encodeURIComponent(slug) +
        '?sort=' + encodeURIComponent(state.sort) + '&page=' + state.page + '&size=' + state.size +
        (state.keyword ? '&keyword=' + encodeURIComponent(state.keyword) : '') +
        (state.category ? '&category=' + encodeURIComponent(state.category) : '');
      return api(url).then(function (data) {
        var store = data.store || {};
        var items = data.items || [];
        var theme = data.theme || {};
        var categories = data.categories || [];
        var total = Number(data.total == null ? items.length : data.total);
        applyTheme(data.theme_css, theme);
        document.title = (store.company_name || '独立站') + ' · 独立商城';
        document.body.dataset.slug = store.slug || slug;
        currentSlug = store.slug || slug;

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

        var filtered = !!(state.keyword || state.category || state.sort !== 'heat');
        var hot = filtered ? [] : items.slice(0, 3);
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
          '<section id="goods"><div class="title"><h2>' + (filtered ? '筛选结果' : '全部商品') + '</h2><span>共 ' + total + ' 个商品</span></div>',
          filterBar(categories),
          items.length ? '<div class="grid">' + items.map(cardHtml).join('') + '</div>' : '<div class="empty"><b>' + (filtered ? '没有符合条件的商品' : '该店铺暂无上架商品') + '</b></div>',
          trust,
          '</section>'
        ].join('');

        root.innerHTML = hero + hotSection + story + all;
        document.body.dataset.storeReady = JSON.stringify({
          slug: store.slug || slug,
          company_name: store.company_name || '',
          preset: theme.preset || '',
          items: items.length,
          total: total,
          first_title: items.length ? items[0].title : '',
          first_href: items.length ? withRef('/p/' + items[0].id) : ''
        });
        document.body.dataset.storeFilter = JSON.stringify({
          keyword: state.keyword,
          category: state.category,
          sort: state.sort,
          total: total,
          categories: categories
        });

        var form = document.getElementById('store-filter');
        if (form) {
          form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            var q = document.getElementById('store-q');
            var cat = document.getElementById('store-cat');
            var sortEl = document.getElementById('store-sort');
            state.keyword = q ? String(q.value || '').trim() : '';
            state.category = cat ? String(cat.value || '') : '';
            state.sort = sortEl ? String(sortEl.value || 'heat') : 'heat';
            state.page = 1;
            syncUrl();
            load(true);
          });
          var reset = document.getElementById('store-reset');
          if (reset) reset.addEventListener('click', function () {
            state.keyword = ''; state.category = ''; state.sort = 'heat'; state.page = 1;
            syncUrl();
            load(true);
          });
        }
        if (scrollToGoods) {
          var box = document.getElementById('goods');
          if (box && box.scrollIntoView) box.scrollIntoView({ block: 'start' });
        }
        if (loadRef().code) trackClick(items.length ? items[0].id : 0);
      }).catch(function (e) {
        root.innerHTML = emptyState('店铺加载失败', e.message);
        document.body.dataset.storeError = e.message;
      });
    }

    return load(false);
  }

  // ── 商品详情页 ──
  function renderProduct(root, id) {
    root.innerHTML = '<div class="loading">正在加载商品…</div>';
    var box = { p: null, m: null };
    return api('/api/shop/products/' + encodeURIComponent(id)).then(function (data) {
      box.p = data.product || {};
      box.m = box.p.merchant || {};
      var __tbSeo = (box.p.specs && box.p.specs._taobao) || {};
      document.title = __tbSeo.seoTitle || box.p.title || '商品详情';
      if (__tbSeo.seoDesc) {
        var __md = document.querySelector('meta[name="description"]');
        if (!__md) { __md = document.createElement('meta'); __md.setAttribute('name', 'description'); document.head.appendChild(__md); }
        __md.setAttribute('content', __tbSeo.seoDesc);
      }
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

      var gallery = ((p.listing && p.listing.gallery) || (p.media && p.media.gallery) || []).filter(Boolean);
      var coverUrl = p.cover_url || (p.listing && p.listing.cover) || gallery[0] || '';
      if (coverUrl && gallery.indexOf(coverUrl) < 0) gallery.unshift(coverUrl);
      var shareUrl = p.buy_url || (location.origin + '/p/' + p.id);
      var storeHref = m.slug ? withRef('/?slug=' + encodeURIComponent(m.slug)) : '';
      var detail = String(p.detail_html || '');
      var detailImgs = (p.listing && p.listing.detail_images) || p.detail_images || [];
      var tb = (p.specs && p.specs._taobao) || {};
      var skus = Array.isArray(tb.sku) ? tb.sku.filter(function (r) { return r && (r.n || r.p); }) : [];
      var promises = Array.isArray(tb.promises) ? tb.promises : [];
      var priceCents = Number(p.price_cents || 0);
      var marketCents = Number(p.market_price_cents || 0);
      var commissionCents = Number(p.commission_estimate_cents || 0);
      var commissionBp = Number(p.commission_bp || 0);
      var rate = commissionBp > 0 ? (commissionBp / 100).toFixed(commissionBp % 100 === 0 ? 0 : 1) + '%' : '';
      var saveCents = marketCents > priceCents ? marketCents - priceCents : 0;
      var tagsHtml = (Array.isArray(p.tags) ? p.tags : []).map(function (t) { return '<span class="plaza-tag">' + esc(t) + '</span>'; }).join('');
      var promisesHtml = promises.map(function (t) { return '<span class="plaza-promise">' + esc(t) + '</span>'; }).join('');
      var skuHtml = skus.map(function (r, i) {
        var cents = Math.round(Number(r.p || 0) * 100);
        return '<button type="button" class="plaza-sku' + (i === 0 ? ' on' : '') + '" onclick="window.__pickSku(this,' + cents + ')">' +
          esc(r.n || ('规格 ' + (i + 1))) + (cents > 0 ? ' · ' + yuan(cents) : '') + (r.s ? ' · 库存 ' + esc(r.s) : '') + '</button>';
      }).join('');
      var specRows = [];
      if (p.brand) specRows.push(['品牌', p.brand]);
      if (p.category) specRows.push(['类目', p.category]);
      if (p.sales) specRows.push(['销量', Number(p.sales) + ' 件']);
      if (p.stock) specRows.push(['库存', Number(p.stock) + ' 件']);
      Object.keys(p.specs || {}).forEach(function (k) {
        if (k === '_taobao') return;
        var v = p.specs[k];
        if (Array.isArray(v)) v = v.join(' / ');
        else if (v && typeof v === 'object') v = JSON.stringify(v);
        if (v != null && String(v).trim()) specRows.push([k, String(v)]);
      });
      if (tb.shipDays) specRows.push(['发货时效', tb.shipDays]);
      if (tb.shipping) specRows.push(['发货方式', tb.shipping]);
      var specsHtml = specRows.length ? specRows.map(function (r) {
        return '<div class="plaza-spec-row"><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>';
      }).join('') : '<div class="plaza-spec-row"><dt>规格参数</dt><dd>商家暂未补充</dd></div>';
      var detailHtml = detail ? cleanHtml(detail) : (detailImgs.length ? detailImgs.map(function (u) {
        return '<img src="' + attr(u) + '" alt="" loading="lazy">';
      }).join('') : '<div class="plaza-empty"><strong>商家暂未上传图文详情</strong><span>可以先看上方商品主图</span></div>');
      var thumbsHtml = gallery.slice(0, 4).map(function (u, i) {
        return '<button type="button" class="plaza-thumb' + (i === 0 ? ' on' : '') + '" onclick="window.__pickThumbImg(this.querySelector(&#39;img&#39;))"><img src="' + attr(u) + '" alt="" loading="lazy"></button>';
      }).join('');
      if (p.media && p.media.video) {
        thumbsHtml += '<button type="button" class="plaza-thumb plaza-video-thumb" title="商品视频"><img src="' + attr(coverUrl) + '" alt=""><span>▶</span></button>';
      }
      var metaHtml = [
        p.brand ? '<span>品牌 <strong>' + esc(p.brand) + '</strong></span>' : '',
        p.category ? '<span>类目 <strong>' + esc(p.category) + '</strong></span>' : '',
        '<span>销量 <strong>' + Number(p.sales || 0) + '</strong></span>',
        '<span>库存 <strong>' + Number(p.stock || 0) + '</strong></span>'
      ].filter(Boolean).join('');

      root.innerHTML = [
        '<div class="plaza-detail-wrap">',
        '<nav class="plaza-crumb"><a href="/plaza.html">首页</a><span class="sep">/</span>',
        storeHref ? '<a href="' + attr(storeHref) + '">' + esc(m.company_name || '商家店铺') + '</a>' : '<span>独立商城</span>',
        '<span class="sep">/</span><span class="current">' + esc(p.title || '商品详情') + '</span></nav>',
        '<div class="plaza-detail-layout">',
        '<section class="plaza-gallery pmedia">',
        '<div class="plaza-main-image">' + (coverUrl ? '<img class="cover" src="' + attr(coverUrl) + '" alt="' + attr(p.title) + '">' : placeholder('商品主图位', 'cover_url')) + '</div>',
        '<div class="plaza-thumbs">' + thumbsHtml + '</div>',
        '</section>',
        '<section class="plaza-info pinfo">',
        '<h1 class="plaza-detail-title">' + esc(p.title || '') + '</h1>',
        p.subtitle ? '<p class="plaza-detail-sub">' + esc(p.subtitle) + '</p>' : '',
        '<div class="plaza-tags">' + tagsHtml + '</div>',
        '<div class="plaza-price-panel">',
        '<div class="plaza-price-line"><strong class="price plaza-price-value"><small>¥</small>' + (priceCents / 100).toFixed(priceCents % 100 === 0 ? 0 : 2) + '</strong>' +
        (marketCents > priceCents ? '<s>' + yuan(marketCents) + '</s><span class="save">立省 ' + yuan(saveCents) + '</span>' : '') + '</div>',
        (commissionCents > 0 || rate) ? '<div class="plaza-commission-row">预计佣金 <b>' + yuan(commissionCents) + '</b><span>佣金率 ' + rate + '</span></div>' : '',
        '</div>',
        '<div class="plaza-detail-meta">' + metaHtml + '</div>',
        skuHtml ? '<div class="plaza-skus">' + skuHtml + '</div>' : '',
        promisesHtml ? '<div class="plaza-promises">' + promisesHtml + '</div>' : '',
        (m.company_name) ? '<div class="plaza-merchant">商家 <span>' + esc(m.company_name) + '</span>' + (storeHref ? '<a href="' + attr(storeHref) + '">进店逛逛</a>' : '') + '</div>' : '',
        '<div class="plaza-order-card">',
        '<form id="order-form">',
        '<div class="plaza-order-row"><label for="qty">数量</label><div class="plaza-qty"><button type="button" id="qtyMinus" aria-label="减少数量">−</button><input id="qty" type="number" min="1" max="' + Math.max(1, Number(p.stock || 0)) + '" value="1"><button type="button" id="qtyPlus" aria-label="增加数量">＋</button></div></div>',
        '<div class="plaza-order-row"><label for="buyer_name">收货人</label><input id="buyer_name" maxlength="64" placeholder="姓名"></div>',
        '<div class="plaza-order-row"><label for="buyer_phone">手机号</label><input id="buyer_phone" maxlength="32" placeholder="用于联系发货"></div>',
        '<div class="plaza-order-row"><label for="buyer_address">收货地址</label><textarea id="buyer_address" rows="2" maxlength="300" placeholder="省 / 市 / 区 + 详细地址"></textarea></div>',
        '<div class="plaza-order-row"><label for="buyer_remark">备注</label><input id="buyer_remark" maxlength="300" placeholder="选填"></div>',
        '<button type="submit" class="plaza-order-submit" id="submit-order"' + (Number(p.stock || 0) <= 0 ? ' disabled' : '') + '>' + (Number(p.stock || 0) <= 0 ? '暂时缺货' : '立即下单') + '</button>',
        '<p class="plaza-order-tip" id="order-tip">当前为到付 / 线下下单通道，提交后商家会联系你确认发货。</p>',
        '</form></div>',
        '<div class="plaza-share"><button type="button" id="copy-link">复制商品链接</button><span id="share-url">' + esc(shareUrl) + '</span></div>',
        '</section></div>',
        '<div class="plaza-detail-sections"><section class="plaza-section">',
        '<div class="plaza-section-head"><h2>商品详情</h2><span>Product Details</span></div>',
        '<div class="plaza-subhead">规格参数</div>',
        '<dl class="plaza-spec-grid">' + specsHtml + '</dl>',
        '<div class="plaza-subhead">图文详情</div>',
        '<div class="plaza-rich">' + detailHtml + '</div>',
        '</section></div>',
        '<div class="plaza-trust"><div><b>平台担保</b>订单由选品广场承接</div><div><b>7 天无理由</b>不影响二次销售可退</div><div><b>到付 / 线下</b>商家联系确认发货</div><div><b>佣金透明</b>推广者按订单实付计佣</div></div>',
        '</div>'
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
      var qtyInput = $('#qty', root);
      var qtyMinus = $('#qtyMinus', root);
      var qtyPlus = $('#qtyPlus', root);
      function adjustQty(delta) {
        if (!qtyInput) return;
        var max = Math.max(1, Number(qtyInput.getAttribute('max') || 1));
        var next = Math.max(1, Math.min(max, (parseInt(qtyInput.value, 10) || 1) + delta));
        qtyInput.value = String(next);
      }
      if (qtyMinus) qtyMinus.addEventListener('click', function () { adjustQty(-1); });
      if (qtyPlus) qtyPlus.addEventListener('click', function () { adjustQty(1); });
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
            tip.className = 'plaza-card-tip ok';
            tip.innerHTML = '下单成功 · 订单号 <b>' + esc(res.order_no) + '</b> · 应付 ' + yuan(res.pay_amount_cents)
              + (Number(res.commission_cents || 0) > 0 ? ' · 本单佣金 ' + yuan(res.commission_cents) : '');
            btn.disabled = false;
          }).catch(function (e) {
            document.body.dataset.lastOrderError = e.message;
            tip.className = 'plaza-card-tip err';
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
  var state = { keyword: '', category: '', sort: 'heat', page: 1, size: 32, loaded: 0, maxBp: 0, categories: [] };

  function plazaPrice(cents) {
    return F.yuan(cents).replace('¥', '');
  }

  function plazaBadge(p) {
    var tags = Array.isArray(p.tags) ? p.tags.join(',') : String(p.tags || '');
    if (/热卖|热销|爆款|榜/.test(tags) || Number(p.sales || 0) >= 100) {
      return '<span class="plaza-card-badge">热销榜</span>';
    }
    if (Number(p.commission_bp || 0) >= 1500) {
      return '<span class="plaza-card-badge blue">高佣</span>';
    }
    return '';
  }

  function plazaCard(p) {
    var id = F.esc(p.id);
    var market = Number(p.market_price_cents || 0) > Number(p.price_cents || 0)
      ? '<s>' + F.yuan(p.market_price_cents) + '</s>' : '';
    var rate = Number(p.commission_bp || 0)
      ? (Number(p.commission_bp) / 100).toFixed(Number(p.commission_bp) % 100 === 0 ? 0 : 1) + '%'
      : '';
    var earn = Number(p.commission_estimate_cents || 0) ? F.yuan(p.commission_estimate_cents) : '';
    var subtitle = p.subtitle || (p.merchant && p.merchant.company_name) || p.category || '';
    return [
      '<article class="plaza-card" data-product="' + id + '">',
      '<div class="plaza-card-media">',
      F.coverOf(p),
      '<div class="plaza-card-badges">' + plazaBadge(p) + '</div>',
      '<div class="plaza-card-overlay"><button type="button" data-copy="' + id + '">获取推广链接</button></div>',
      '</div>',
      '<div class="plaza-card-body">',
      '<h4 class="plaza-card-title">' + F.esc(p.title || '') + '</h4>',
      '<p class="plaza-card-sub">' + F.esc(subtitle) + '</p>',
      '<div class="plaza-card-foot">',
      '<div class="plaza-card-price"><div class="plaza-card-price-label">买家到手价</div>',
      '<div class="plaza-card-price-line"><strong><small>¥</small>' + plazaPrice(p.price_cents) + '</strong>' + market + '</div></div>',
      '<div class="plaza-card-commission">' + (rate ? '<span class="plaza-card-rate">佣金 ' + rate + '</span>' : '') +
      (earn ? '<span class="plaza-card-earn">赚 ' + earn + '</span>' : '') + '</div>',
      '</div>',
      '<div class="plaza-card-tip" data-tip="' + id + '"></div>',
      '</div></article>'
    ].join('');
  }

  function bindPlazaCards(root) {
    Array.prototype.forEach.call(root.querySelectorAll('[data-product]'), function (card) {
      if (card.dataset.plazaBound === '1') return;
      card.dataset.plazaBound = '1';
      card.addEventListener('click', function (ev) {
        if (ev.target.closest('[data-copy]')) return;
        location.href = F.withRef('/p/' + card.getAttribute('data-product'));
      });
    });
  }

  function bindCopy(root) {
    Array.prototype.forEach.call(root.querySelectorAll('[data-copy]'), function (btn) {
      btn.addEventListener('click', function (event) {
        if (event) { event.preventDefault(); event.stopPropagation(); }
        var pid = btn.getAttribute('data-copy');
        var tip = root.querySelector('[data-tip="' + pid + '"]');
        var plain = location.origin + '/p/' + pid;
        btn.disabled = true;
        tip.className = 'plaza-card-tip';
        tip.textContent = '正在生成推广链接…';
        authed('/api/shop/plaza/link', { method: 'POST', body: JSON.stringify({ product_id: Number(pid), source: 'online_plaza' }) })
          .then(function (r) {
            copyText(r.link).catch(function () {});
            document.body.dataset.lastLink = JSON.stringify(r);
            tip.className = 'plaza-card-tip ok';
            tip.innerHTML = '已复制推广链接 · 预计佣金 ' + F.yuan(r.commission_estimate_cents)
              + ' (' + (Number(r.commission_bp || 0) / 100) + '%)<br><span class="muted">' + F.esc(r.link) + '</span>';
            btn.textContent = '已复制';
            btn.disabled = false;
          })
          .catch(function (e) {
            document.body.dataset.lastLinkError = e.message + '#' + (e.status || 0);
            copyText(plain).catch(function () {});
            tip.className = 'plaza-card-tip err';
            tip.textContent = (e.status === 401 || /authenticat|凭证/i.test(e.message))
              ? '需要登录态：已复制普通商品链接 ' + plain + '；登录后可生成带归因 (?r=&rf=) 的推广链接。'
              : '生成失败：' + e.message + '（已复制普通商品链接 ' + plain + '）';
            btn.disabled = false;
          });
      });
    });
  }

  function updatePlazaStats(items, total) {
    var totalEl = document.getElementById('plaza-total');
    if (totalEl) totalEl.textContent = total > 0 ? Number(total).toLocaleString('zh-CN') + '+' : '0';
    var maxBp = Number(state.maxBp || 0);
    var maxEl = document.getElementById('plaza-max-rate');
    if (maxEl) maxEl.textContent = '最高 ' + (maxBp > 0 ? (maxBp / 100).toFixed(maxBp % 100 === 0 ? 0 : 1) + '%' : '45%');
    var count = document.getElementById('plaza-count');
    if (count) count.textContent = '显示 1-' + state.loaded + ' 个商品，共 ' + Number(total || 0).toLocaleString('zh-CN') + ' 个';
  }

  function renderPlazaMore(total) {
    var box = document.getElementById('plaza-pager');
    if (!box) return;
    if (state.loaded < total) {
      box.innerHTML = '<button type="button" id="plazaLoadMore">加载更多</button>';
      var btn = document.getElementById('plazaLoadMore');
      if (btn) btn.addEventListener('click', function () {
        state.page += 1;
        load(document.getElementById('app'), true);
      });
    } else {
      box.innerHTML = '';
    }
  }

  function load(root, append) {
    if (!root) root = document.getElementById('app');
    if (!root) return Promise.resolve();
    if (!append) {
      state.page = 1;
      state.loaded = 0;
      state.maxBp = 0;
      root.innerHTML = '<div class="plaza-empty"><strong>正在加载选品广场…</strong></div>';
    }
    var url = '/api/shop/plaza?sort=' + encodeURIComponent(state.sort) + '&page=' + state.page + '&size=' + state.size
      + (state.keyword ? '&keyword=' + encodeURIComponent(state.keyword) : '')
      + (state.category ? '&category=' + encodeURIComponent(state.category) : '');
    return F.api(url).then(function (d) {
      var items = d.items || [];
      var total = Number(d.total || 0);
      var seen = {};
      (state.categories || []).forEach(function (c) { if (c) seen[c] = 1; });
      items.forEach(function (p) { if (p.category) seen[p.category] = 1; });
      state.categories = Object.keys(seen);
      var catEl = document.getElementById('cat');
      if (catEl) {
        catEl.innerHTML = '<option value="">全部类目</option>' + state.categories.map(function (c) {
          return '<option value="' + F.attr(c) + '"' + (c === state.category ? ' selected' : '') + '>' + F.esc(c) + '</option>';
        }).join('');
      }
      state.loaded += items.length;
      items.forEach(function (p) { state.maxBp = Math.max(Number(state.maxBp || 0), Number(p.commission_bp || 0)); });
      if (append) {
        root.insertAdjacentHTML('beforeend', items.map(plazaCard).join(''));
      } else {
        root.innerHTML = items.length ? items.map(plazaCard).join('')
          : '<div class="plaza-empty"><strong>没有匹配的商品</strong><span>换个关键词或类目再试</span></div>';
      }
      updatePlazaStats(items, total);
      renderPlazaMore(total);
      document.body.dataset.plazaReady = JSON.stringify({ total: total, items: state.loaded, cats: state.categories });
      bindCopy(root);
      bindPlazaCards(root);
    }).catch(function (e) {
      if (!append) root.innerHTML = '<div class="plaza-empty"><strong>选品广场加载失败</strong><span>' + F.esc(e.message) + '</span></div>';
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
      load(document.getElementById('app'), false);
    });
  }
  /* 登录收进顶栏弹窗：页面中间只放商品，不再用一行登录表单占位 */
  var authOpen = document.getElementById('auth-open');
  var authLogout = document.getElementById('auth-logout');
  var authBadge = document.getElementById('auth-state');
  var authModal = document.getElementById('auth-modal');
  var loginTip = document.getElementById('plaza-login-tip');

  function closeAuthModal() {
    if (!authModal) return;
    authModal.hidden = true;
    document.body.classList.remove('auth-modal-open');
  }

  function openAuthModal() {
    if (!authModal) return;
    authModal.hidden = false;
    document.body.classList.add('auth-modal-open');
    var acct = document.getElementById('plaza-acct');
    if (acct) acct.focus();
  }

  function refreshAuthChrome() {
    var signed = !!token();
    if (authOpen) authOpen.hidden = signed;
    if (authLogout) authLogout.hidden = !signed;
    if (authBadge) authBadge.textContent = signed ? '已登录' : '';
    if (signed) closeAuthModal();
  }

  if (authOpen) authOpen.addEventListener('click', openAuthModal);
  if (authLogout) authLogout.addEventListener('click', function () {
    try { localStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(TOKEN_KEY); } catch (e) {}
    location.reload();
  });
  if (authModal) {
    authModal.addEventListener('click', function (ev) {
      var target = ev.target;
      if (!target || target === authModal) { closeAuthModal(); return; }
      if (target.getAttribute && target.getAttribute('data-auth-close') === '1') closeAuthModal();
    });
  }
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') closeAuthModal(); });

  var pbtn = document.getElementById('plaza-login');
  if (pbtn) pbtn.addEventListener('click', function () {
    var acct = (document.getElementById('plaza-acct').value || '').trim();
    var pwd = document.getElementById('plaza-pwd').value || '';
    var tip = loginTip || { className: '', textContent: '' };
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
  refreshAuthChrome();
  document.body.dataset.hasToken = token() ? '1' : '0';
  load(document.getElementById('app'));
})();