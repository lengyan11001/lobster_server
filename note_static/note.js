(function () {
  'use strict';
  var TOKEN_KEY = 'note_token';
  var BRAND = (new URLSearchParams(location.search).get('brand') || localStorage.getItem('note_brand') || 'bihuo').toLowerCase();
  try { localStorage.setItem('note_brand', BRAND); } catch (e) {}

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function token() { try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; } }
  function setToken(t) { try { localStorage.setItem(TOKEN_KEY, t); } catch (e) {} }
  function clearToken() { try { localStorage.removeItem(TOKEN_KEY); } catch (e) {} }

  function installationId() {
    var key = 'note_installation_id';
    var v = '';
    try { v = localStorage.getItem(key) || ''; } catch (e) { v = ''; }
    if (!v) {
      v = 'note-web-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
      try { localStorage.setItem(key, v); } catch (e) {}
    }
    return v;
  }

  function headers(extra) {
    var h = Object.assign({ 'X-Lobster-Brand': BRAND }, extra || {});
    if (!h['X-Installation-Id']) h['X-Installation-Id'] = installationId();
    var t = token();
    if (t) h['Authorization'] = 'Bearer ' + t;
    return h;
  }

  async function renew() {
    var t = token();
    if (!t) return false;
    try {
      var r = await fetch('/auth/refresh', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + t, 'X-Lobster-Brand': BRAND, 'X-Installation-Id': installationId(), 'Content-Type': 'application/json' },
        body: '{}'
      });
      if (!r.ok) return false;
      var d = await r.json();
      if (!d || !d.access_token) return false;
      setToken(d.access_token);
      return true;
    } catch (e) { return false; }
  }

  async function api(path, opts, retry) {
    var o = Object.assign({}, opts || {});
    o.headers = headers(o.headers);
    var r = await fetch(path, o);
    if (r.status === 401 && retry !== false) {
      if (await renew()) return api(path, opts, false);
      throw Object.assign(new Error('登录状态已失效，请重新登录'), { status: 401 });
    }
    return r;
  }

  async function apiJson(path, opts) {
    var r = await api(path, opts);
    var d = null;
    try { d = await r.json(); } catch (e) { d = null; }
    if (!r.ok) throw Object.assign(new Error(friendlyError((d && (d.detail || d.msg)) || ('请求失败 ' + r.status))), { status: r.status, data: d });
    return d;
  }

  var state = {
    items: [], total: 0, me: null, tab: 'home', query: '', statusFilter: '', detail: null, pick: null,
    media: null, chunks: [], startedAt: 0, tickId: 0, polling: 0, statuses: {}
  };

  /* ---------- toast ---------- */
  function toast(text) {
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(function () { el.classList.add('show'); }, 10);
    setTimeout(function () { el.classList.remove('show'); setTimeout(function(){ el.remove(); }, 200); }, 1800);
  }

  /* ---------- sheets ---------- */
  function openSheet(id, title, bodyHtml, opts) {
    opts = opts || {};
    closeSheet(id);
    var layer = document.createElement('div');
    layer.className = 'sheet-layer';
    layer.setAttribute('data-sheet', id);
    layer.innerHTML =
      '<div class="sheet-backdrop" data-close="' + esc(id) + '"></div>' +
      '<section class="sheet' + (opts.tall ? ' tall' : '') + '" role="dialog" aria-modal="true">' +
      '<header class="sheet-head"><h3>' + esc(title) + '</h3>' +
      '<button type="button" class="sheet-close" data-close="' + esc(id) + '">关闭</button></header>' +
      '<div class="sheet-body" data-body="' + esc(id) + '">' + (bodyHtml || '') + '</div>' +
      '</section>';
    $('modalRoot').appendChild(layer);
    document.body.classList.add('sheet-open');
    requestAnimationFrame(function () { layer.classList.add('show'); });
    return layer;
  }

  function closeSheet(id) {
    var layer = id
      ? document.querySelector('.sheet-layer[data-sheet="' + id + '"]')
      : document.querySelector('.sheet-layer:last-child');
    if (!layer) return;
    layer.classList.remove('show');
    setTimeout(function () {
      layer.remove();
      if (!document.querySelector('.sheet-layer')) document.body.classList.remove('sheet-open');
    }, 160);
  }

  function sheetBody(id) { return document.querySelector('[data-body="' + id + '"]'); }

  /* ---------- auth ---------- */
  async function doLogin() {
    var account = ($('account').value || '').trim();
    var password = $('password').value || '';
    var msg = $('loginMsg');
    msg.textContent = '';
    if (!account || !password) { msg.textContent = '请输入账号和密码'; return; }
    $('loginBtn').disabled = true;
    try {
      var d = await apiJson('/auth/login-phone-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account: account, password: password, brand_mark: BRAND })
      });
      if (!d || !d.access_token) throw new Error('登录失败');
      setToken(d.access_token);
      await bootApp();
    } catch (e) {
      msg.textContent = e && e.message ? e.message : '登录失败';
    } finally {
      $('loginBtn').disabled = false;
    }
  }

  function fmtCredits(v) {
    var n = Number(v);
    if (!isFinite(n)) n = 0;
    if (n >= 10000) return (Math.round(n / 1000) / 10).toFixed(1).replace(/\.0$/, '') + '万';
    return (Math.round(n * 100) / 100).toFixed(2).replace(/\.?0+$/, '');
  }

  function isCreditError(msg) {
    return /积分不足|余额不足|402|算力/.test(String(msg || ''));
  }

  function friendlyError(msg) {
    if (isCreditError(msg)) return '余额不足，请先充值后再试';
    var s = String(msg || '').trim();
    return s || '操作失败';
  }

  function renderBalance() {
    var n = Number((state.me && state.me.credits) || 0);
    var chip = $('creditText');
    if (chip) {
      chip.textContent = '积分 ' + fmtCredits(n);
      chip.classList.toggle('low', n < 10);
    }
    var mine = $('mineCredits');
    if (mine) mine.textContent = fmtCredits(n);
  }

  async function refreshBalance() {
    try {
      state.me = await apiJson('/auth/me');
      renderBalance();
    } catch (e) {}
  }
  async function loadMe() {
    state.me = await apiJson('/auth/me');
    var name = (state.me && (state.me.email || state.me.phone || state.me.nickname)) || '';
    $('whoText').textContent = name;
    $('mineName').textContent = name || '未登录';
    $('mineBrand').textContent = '品牌：' + BRAND;
    $('mineAvatar').textContent = (name || '必').slice(0, 1).toUpperCase();
    renderBalance();
    var h = new Date().getHours();
    $('homeGreeting').textContent = (h < 11 ? '早上好' : (h < 18 ? '下午好' : '晚上好')) + '，' + (name.split('@')[0] || '');
    return state.me;
  }

  function showLogin() {
    $('appView').classList.add('hidden');
    $('loginView').classList.remove('hidden');
  }

  async function bootApp() {
    if (!token()) { showLogin(); return; }
    try {
      await loadMe();
      $('loginView').classList.add('hidden');
      $('appView').classList.remove('hidden');
      await loadRecords();
    } catch (e) {
      if (e && e.status === 401) { clearToken(); showLogin(); }
      else { $('loginMsg').textContent = (e && e.message) || '加载失败'; }
    }
  }

  /* ---------- tabs ---------- */
  function setTab(tab) {
    state.tab = tab;
    ['home', 'records', 'mine'].forEach(function (k) {
      var v = $('view' + k.charAt(0).toUpperCase() + k.slice(1));
      if (v) v.classList.toggle('hidden', k !== tab);
    });
    Array.prototype.forEach.call(document.querySelectorAll('#tabbar .tab'), function (b) {
      b.classList.toggle('active', b.getAttribute('data-tab') === tab);
    });
    if (tab === 'records') renderRecords();
    if (tab === 'mine') { renderMine(); refreshBalance(); }
  }

  /* ---------- records ---------- */
  function fmtTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString('zh-CN', { hour12: false });
  }
  function dayKey(iso) {
    var d = iso ? new Date(iso) : null;
    if (!d || isNaN(d.getTime())) return '';
    return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
  }
  function todayKey() {
    var d = new Date();
    return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate();
  }
  function statusChip(s) {
    var map = { processing: '整理中', pending: '排队中', queued: '排队中', completed: '已完成', failed: '失败' };
    var key = map[s] ? s : 'processing';
    return '<span class="chip ' + esc(key) + '">' + esc(map[s] || s || '未知') + '</span>';
  }

  async function loadRecords() {
    var d = await apiJson('/api/h5/recorder/files?page=1&page_size=50');
    var prev = state.statuses || {};
    (d && d.items ? d.items : []).forEach(function (it) {
      var key = String(it.id);
      var old = prev[key];
      if (old && old !== 'failed' && it.status === 'failed') {
        toast(friendlyError(it.error_message));
        if (isCreditError(it.error_message)) refreshBalance();
      } else if (old && old !== 'completed' && it.status === 'completed') {
        refreshBalance();
      }
      prev[key] = it.status;
    });
    state.statuses = prev;
    state.items = (d && d.items) || [];
    state.total = (d && d.total) || state.items.length;
    renderAll();
    schedulePoll();
  }

  function renderAll() {
    renderHome();
    if (state.tab === 'records') renderRecords();
    if (state.tab === 'mine') renderMine();
  }

  function renderHome() {
    var tk = todayKey();
    var today = state.items.filter(function (it) { return dayKey(it.recorded_at || it.created_at) === tk; });
    var done = today.filter(function (it) { return it.status === 'completed'; }).length;
    var doing = today.filter(function (it) { return it.status !== 'completed' && it.status !== 'failed'; }).length;
    var points = today.reduce(function (n, it) { return n + (Array.isArray(it.key_points) ? it.key_points.length : 0); }, 0);
    $('statToday').textContent = today.length;
    $('statDone').textContent = done;
    $('statDoing').textContent = doing;
    $('statPoints').textContent = points;
    $('homeSub').textContent = today.length
      ? '今天录了 ' + today.length + ' 条，AI 已整理 ' + done + ' 条' + (doing ? '，还有 ' + doing + ' 条在处理' : '')
      : '今天还没有录音，点下面的按钮开始';

    var recent = state.items.slice(0, 3);
    var box = $('recentList');
    var empty = $('recentEmpty');
    if (!recent.length) { box.innerHTML = ''; empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    box.innerHTML = recent.map(recordHtml).join('');
  }

  function recordHtml(it) {
    var err = it.status === 'failed' && it.error_message
      ? '<div class="record-err">' + esc(friendlyError(it.error_message)) + '</div>'
      : '';
    return '<div class="record" data-id="' + esc(it.id) + '">' +
      '<div class="meta"><div class="name">' + esc(it.display_name || it.file_name || ('录音 #' + it.id)) + '</div>' +
      '<div class="sub">' + statusChip(it.status) +
      '<span>' + esc(fmtTime(it.recorded_at || it.created_at)) + '</span>' +
      (it.file_size ? '<span>' + Math.round(it.file_size / 1024) + ' KB</span>' : '') +
      '</div>' + err + '</div>' +
      '<div class="ops">' +
      '<button type="button" data-act="play">播放</button>' +
      '<button type="button" data-act="detail">详情</button>' +
      (it.status === 'failed' ? '<button type="button" data-act="retry">重试</button>' : '') +
      '<button type="button" data-act="delete" class="danger">删除</button>' +
      '</div></div>';
  }

  function filteredItems() {
    var q = String(state.query || '').trim().toLowerCase();
    return state.items.filter(function (it) {
      if (state.statusFilter) {
        if (state.statusFilter === 'processing') {
          if (it.status === 'completed' || it.status === 'failed') return false;
        } else if (it.status !== state.statusFilter) {
          return false;
        }
      }
      if (!q) return true;
      var hay = [it.display_name, it.file_name, it.summary_text, it.device_name].join(' ').toLowerCase();
      return hay.indexOf(q) >= 0;
    });
  }

  function renderRecords() {
    var rows = filteredItems();
    var box = $('records');
    var empty = $('empty');
    if (!rows.length) { box.innerHTML = ''; empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    box.innerHTML = rows.map(recordHtml).join('');
  }

  function renderMine() {
    var size = state.items.reduce(function (n, it) { return n + (Number(it.file_size) || 0); }, 0);
    $('mineTotal').textContent = state.total;
    $('mineSize').textContent = size ? (size / 1024 / 1024).toFixed(1) + ' MB' : '0 MB';
  }

  function findItem(id) {
    return state.items.filter(function (x) { return String(x.id) === String(id); })[0];
  }

  async function playItem(it) {
    var r = await api('/api/h5/recorder/files/' + encodeURIComponent(it.id) + '/audio');
    if (!r.ok) throw new Error('音频加载失败');
    var blob = await r.blob();
    var url = URL.createObjectURL(blob);
    var audio = new Audio(url);
    audio.onended = function () { URL.revokeObjectURL(url); };
    audio.play();
  }

  /* ---------- detail sheet ---------- */
  async function openDetail(id) {
    var d = await apiJson('/api/h5/recorder/files/' + encodeURIComponent(id));
    state.detail = d;
    var analysis = d.analysis && typeof d.analysis === 'object' ? d.analysis : {};
    var todos = Array.isArray(analysis.todos) ? analysis.todos : [];
    var points = Array.isArray(d.key_points) ? d.key_points : [];
    var html = '';
    html += '<div class="kv">' + esc(fmtTime(d.recorded_at || d.created_at)) + (d.source_label ? ' · ' + esc(d.source_label) : '') + '</div>';
    if (d.status === 'failed' && d.error_message) {
      html += '<div class="error-box">' + esc(friendlyError(d.error_message)) + '</div>';
    }
    if (d.summary_text) html += '<div class="summary-box">' + esc(d.summary_text) + '</div>';
    if (points.length) {
      html += '<h4>要点</h4><ul class="mini-list">' + points.slice(0, 3).map(function (p) {
        return '<li>' + esc(typeof p === 'string' ? p : JSON.stringify(p)) + '</li>';
      }).join('') + '</ul>' + (points.length > 3 ? '<div class="kv">还有 ' + (points.length - 3) + ' 条</div>' : '');
    }
    html += '<h4>快捷操作</h4><div class="action-grid">' +
      '<button type="button" data-detail-act="play">▶ 播放</button>' +
      '<button type="button" data-detail-act="translate">翻译</button>' +
      '<button type="button" data-detail-act="ask">问 AI</button>' +
      '<button type="button" data-detail-act="export">导出</button>' +
      '</div>';
    html += '<h4>更多</h4><div class="action-grid">' +
      '<button type="button" data-detail-act="todos">待办' + (todos.length ? ' · ' + todos.length : '') + '</button>' +
      '<button type="button" data-detail-act="outline">大纲</button>' +
      '<button type="button" data-detail-act="transcript">逐字稿' + ((d.transcript_segments || []).length ? ' · ' + (d.transcript_segments || []).length + ' 段' : '') + '</button>' +
      '<button type="button" data-detail-act="analyze">重新分析</button>' +
      '</div>';
    openSheet('detail', d.display_name || d.file_name || '录音详情', html, { tall: true });
  }

  function handleDetailAct(act) {
    var d = state.detail;
    if (!d) return;
    if (act === 'play') { playItem(d).catch(function (e) { toast(e.message); }); return; }
    if (act === 'translate') { openTranslateSheet(d); return; }
    if (act === 'ask') { openAskSheet(d); return; }
    if (act === 'export') { openExportSheet(d); return; }
    if (act === 'todos') { openTodosSheet(d); return; }
    if (act === 'outline') { openOutlineSheet(d); return; }
    if (act === 'transcript') { openTranscriptSheet(d); return; }
    if (act === 'analyze') {
      closeSheet('todos');
      closeSheet('outline');
      closeSheet('summary');
      toast('正在重新分析…');
      apiJson('/api/h5/recorder/files/' + d.id + '/analyze', { method: 'POST' })
        .then(function (r) { state.detail = r.record; openDetail(d.id); toast('已重新整理'); refreshBalance(); loadRecords().catch(function(){}); })
        .catch(function (e) { toast(e.message); });
    }
  }

  function renderOutline(nodes) {
    if (!Array.isArray(nodes)) return '';
    return '<ul class="outline">' + nodes.map(function (n) {
      if (typeof n === 'string') return '<li>' + esc(n) + '</li>';
      var kids = n.children || n.items || [];
      return '<li>' + esc(n.title || n.name || '') + (Array.isArray(kids) && kids.length ? renderOutline(kids) : '') + '</li>';
    }).join('') + '</ul>';
  }

  function openTodosSheet(d) {
    var todoList = (d.analysis && Array.isArray(d.analysis.todos)) ? d.analysis.todos : [];
    var html = todoList.length
      ? '<ul class="todo-list">' + todoList.map(function (t) {
          if (typeof t === 'string') return '<li>' + esc(t) + '</li>';
          var meta = [t.owner, t.due].filter(Boolean).join(' · ');
          return '<li><b>' + esc(t.title || '') + '</b>' + (meta ? '<span>' + esc(meta) + '</span>' : '') + '</li>';
        }).join('') + '</ul>'
      : '<div class="empty">这条录音没有识别到待办</div>';
    html += '<div class="action-grid" style="margin-top:14px">' +
      '<button type="button" data-detail-act="analyze">AI 重新整理</button>' +
      '<button type="button" data-detail-act="outline">看大纲</button></div>';
    openSheet('todos', '待办', recordLabel(d) + html);
  }

  function openOutlineSheet(d) {
    var outline = (d.analysis && Array.isArray(d.analysis.outline)) ? d.analysis.outline : [];
    var html = outline.length ? renderOutline(outline) : '<div class="empty">这条录音还没有大纲</div>';
    html += '<div class="action-grid" style="margin-top:14px">' +
      '<button type="button" data-detail-act="analyze">AI 生成大纲</button>' +
      '<button type="button" data-detail-act="todos">看待办</button></div>';
    openSheet('outline', '大纲 / 思维导图', recordLabel(d) + html, { tall: true });
  }

  function openTranscriptSheet(d) {
    var segs = Array.isArray(d.transcript_segments) ? d.transcript_segments : [];
    var html = segs.length
      ? '<div class="segments">' + segs.map(function (s) {
          var who = s && (s.speaker || s.speaker_id || '');
          var text = s && (s.text || s.content || '');
          var at = s && (s.start_ms || s.start || 0);
          var clock = at ? (Math.floor(at / 60000) + ':' + String(Math.floor((at % 60000) / 1000)).padStart(2, '0')) : '';
          return '<div class="seg"><b>' + esc(who || '未知') + '</b>' + (clock ? '<i>' + esc(clock) + '</i>' : '') + '<span>' + esc(text) + '</span></div>';
        }).join('') + '</div>'
      : (d.transcript_text ? '<div class="transcript">' + esc(d.transcript_text) + '</div>' : '<div class="empty">还没有转写内容</div>');
    openSheet('transcript', '逐字稿', html, { tall: true });
  }

  function openTranslateSheet(d) {
    var html = '';
    if (d.translated_text) {
      html += '<div class="transcript">' + esc(d.translated_text) + '</div><div class="kv">目标语言：' + esc(d.translate_lang || '') + '</div>' +
        '<div class="action-grid" style="margin-top:14px"><button type="button" data-tr-retranslate="1">换语言重新翻译</button></div>';
    } else {
      html += '<div class="inline-row"><select id="trLang">' +
        ['English', '日本語', '한국어', 'Русский', 'Español', 'Français', 'Deutsch', 'العربية']
          .map(function (l) { return '<option>' + l + '</option>'; }).join('') +
        '</select><button type="button" data-tr="translate">翻译全文</button></div>';
    }
    openSheet('translate', '翻译', recordLabel(d) + html);
  }

  function openAskSheet(d) {
    var html = '<div class="inline-row"><input id="askInput" placeholder="例如：客户最关心什么？"><button type="button" data-tr="ask">提问</button></div>' +
      '<div id="askAnswer" class="ask-answer"></div>';
    openSheet('ask', '问这条录音', recordLabel(d) + html, { tall: true });
  }

  function openExportSheet(d) {
    var html = '<div class="action-grid">' +
      '<button type="button" data-export="txt">TXT</button>' +
      '<button type="button" data-export="md">Markdown</button>' +
      '<button type="button" data-export="srt">字幕 SRT</button>' +
      '<button type="button" data-export="docx">Word</button>' +
      '</div><div class="kv" style="margin-top:10px">导出内容包含摘要、要点、待办、逐字稿与译文。</div>';
    openSheet('export', '导出', recordLabel(d) + html);
  }

  function detailText(d, markdown) {
    var lines = [];
    var title = d.display_name || d.file_name || ('录音 #' + d.id);
    var analysis = d.analysis && typeof d.analysis === 'object' ? d.analysis : {};
    var todos = Array.isArray(analysis.todos) ? analysis.todos : [];
    var outline = Array.isArray(analysis.outline) ? analysis.outline : [];
    if (markdown) {
      lines.push('# ' + title, '', '- 时间：' + fmtTime(d.recorded_at || d.created_at), '- 来源：' + (d.source_label || ''), '');
      if (d.summary_text) lines.push('## 摘要', '', d.summary_text, '');
      if (Array.isArray(d.key_points) && d.key_points.length) {
        lines.push('## 要点', '');
        d.key_points.forEach(function (p) { lines.push('- ' + (typeof p === 'string' ? p : JSON.stringify(p))); });
        lines.push('');
      }
      if (todos.length) {
        lines.push('## 待办', '');
        todos.forEach(function (t) {
          if (typeof t === 'string') { lines.push('- ' + t); return; }
          var meta = [t.owner, t.due].filter(Boolean).join('，');
          lines.push('- ' + (t.title || '') + (meta ? '（' + meta + '）' : ''));
        });
        lines.push('');
      }
      if (outline.length) {
        var render = function (nodes, depth) {
          nodes.forEach(function (n) {
            if (typeof n === 'string') { lines.push('  '.repeat(depth) + '- ' + n); return; }
            lines.push('  '.repeat(depth) + '- ' + (n.title || n.name || ''));
            if (Array.isArray(n.children)) render(n.children, depth + 1);
          });
        };
        lines.push('## 大纲', '');
        render(outline, 0);
        lines.push('');
      }
      if (Array.isArray(d.segments) && d.segments.length) {
        lines.push('## 逐字稿', '');
        d.segments.forEach(function (s) { lines.push('**' + (s.speaker || '未知') + '**：' + (s.text || s.content || '')); });
        lines.push('');
      } else if (d.transcript_text) {
        lines.push('## 逐字稿', '', d.transcript_text, '');
      }
      if (d.translated_text) lines.push('## 译文（' + (d.translate_lang || '') + '）', '', d.translated_text, '');
    } else {
      lines.push(title, '时间：' + fmtTime(d.recorded_at || d.created_at), '来源：' + (d.source_label || ''));
      if (d.summary_text) lines.push('', '【摘要】', d.summary_text);
      if (Array.isArray(d.key_points) && d.key_points.length) {
        lines.push('', '【要点】');
        d.key_points.forEach(function (p) { lines.push('- ' + (typeof p === 'string' ? p : JSON.stringify(p))); });
      }
      if (todos.length) {
        lines.push('', '【待办】');
        todos.forEach(function (t) {
          if (typeof t === 'string') { lines.push('- ' + t); return; }
          lines.push('- ' + (t.title || '') + [t.owner, t.due].filter(Boolean).join(' '));
        });
      }
      if (Array.isArray(d.segments) && d.segments.length) {
        lines.push('', '【逐字稿】');
        d.segments.forEach(function (s) { lines.push('[' + (s.speaker || '未知') + '] ' + (s.text || s.content || '')); });
      } else if (d.transcript_text) {
        lines.push('', '【逐字稿】', d.transcript_text);
      }
      if (d.translated_text) lines.push('', '【译文】', d.translated_text);
    }
    return lines.join('\n');
  }

  function exportDetail(fmt) {
    var d = state.detail;
    if (!d) return;
    var text = detailText(d, fmt === 'md');
    var blob = new Blob([text], { type: fmt === 'md' ? 'text/markdown;charset=utf-8' : 'text/plain;charset=utf-8' });
    var a = document.createElement('a');
    var name = (d.display_name || d.file_name || ('note-' + d.id)).replace(/[\\/:*?"<>|]/g, '_');
    a.href = URL.createObjectURL(blob);
    a.download = name + (fmt === 'md' ? '.md' : '.txt');
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  async function exportServer(fmt) {
    var d = state.detail;
    if (!d) return;
    var r = await api('/api/h5/recorder/files/' + d.id + '/export?format=' + encodeURIComponent(fmt));
    if (!r.ok) {
      var err = null;
      try { err = await r.json(); } catch (e) { err = null; }
      throw new Error((err && err.detail) || '导出失败');
    }
    var blob = await r.blob();
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (d.display_name || d.file_name || ('note-' + d.id)) + '.' + fmt;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  /* ---------- home capability entries ---------- */
  function recordLabel(d) {
    return '<div class="kv" style="margin-bottom:10px">' +
      esc(d.display_name || d.file_name || ('录音 #' + d.id)) +
      ' · ' + esc(fmtTime(d.recorded_at || d.created_at)) + '</div>';
  }
  function openRecordPicker(title, hint, run) {
    var items = state.items.slice(0, 30);
    var html = hint ? '<div class="kv" style="margin-bottom:10px">' + esc(hint) + '</div>' : '';
    html += '<div class="record-pick">' + items.map(function (it) {
      return '<button type="button" data-pick-id="' + esc(it.id) + '"><b>' +
        esc(it.display_name || it.file_name || ('录音 #' + it.id)) + '</b><span>' +
        esc(fmtTime(it.recorded_at || it.created_at)) +
        (it.status ? ' · ' + esc(({done:'已完成',completed:'已完成',processing:'整理中',pending:'排队中',failed:'失败'})[it.status] || it.status) : '') + '</span></button>';
    }).join('') + '</div>';
    state.pick = run;
    openSheet('picker', title, html, { tall: true });
  }

  function openNoRecordSheet(title, message) {
    openSheet('empty', title,
      '<div class="empty">' + esc(message) + '</div>' +
      '<div class="action-grid" style="margin-top:16px">' +
      '<button type="button" data-empty-action="record">去首页录音</button>' +
      '<button type="button" data-empty-action="upload">上传音频文件</button>' +
      '</div>');
  }

  function runOnRecord(title, hint, run) {
    if (!state.items.length) { openNoRecordSheet(title, '还没有录音。先录一条或上传一个音频文件。'); return; }
    if (state.items.length === 1) {
      var only = state.items[0];
      apiJson('/api/h5/recorder/files/' + encodeURIComponent(only.id)).then(function (d) {
        state.detail = d;
        run(d);
      }).catch(function (e) { toast(e.message); });
      return;
    }
    openRecordPicker(title, hint, async function (it) {
      var d = await apiJson('/api/h5/recorder/files/' + encodeURIComponent(it.id));
      state.detail = d;
      run(d);
    });
  }

  function openSummarySheet(d) {
    var points = Array.isArray(d.key_points) ? d.key_points : [];
    var html = d.summary_text
      ? '<div class="summary-box">' + esc(d.summary_text) + '</div>'
      : '<div class="empty">这条录音还没有摘要</div>';
    if (points.length) {
      html += '<h4>关键要点</h4><ul class="mini-list">' + points.map(function (p) {
        return '<li>' + esc(typeof p === 'string' ? p : JSON.stringify(p)) + '</li>';
      }).join('') + '</ul>';
    }
    html += '<div class="action-grid" style="margin-top:16px">' +
      '<button type="button" data-detail-act="todos">查看待办</button>' +
      '<button type="button" data-detail-act="outline">查看大纲</button></div>';
    openSheet('summary', '总结要点', recordLabel(d) + html, { tall: true });
  }

  function openTranscribeSheet(d) {
    var segs = Array.isArray(d.transcript_segments) ? d.transcript_segments : (Array.isArray(d.segments) ? d.segments : []);
    var text = d.transcript_text || '';
    var html;
    if (segs.length) {
      html = '<div class="transcript">' + esc(segs.map(function (s) {
        return (s && (s.text || s.content)) || '';
      }).filter(Boolean).join('\n')) + '</div>';
    } else if (text) {
      html = '<div class="transcript">' + esc(text) + '</div>';
    } else {
      html = '<div class="empty">还没有转写内容</div>';
    }
    openSheet('transcribe', 'AI 转写', recordLabel(d) + html, { tall: true });
  }

  function openSpeakersSheet(d) {
    var segs = Array.isArray(d.transcript_segments) ? d.transcript_segments : (Array.isArray(d.segments) ? d.segments : []);
    if (!segs.length) {
      return openSheet('speakers', '区分发言人', '<div class="empty">还没有可区分发言人的逐字稿</div>');
    }
    var who = {};
    segs.forEach(function (s) { var k = (s && (s.speaker || s.speaker_id)) || '未知'; who[k] = 1; });
    var html = '<div class="kv" style="margin-bottom:10px">共 ' + Object.keys(who).length + ' 位发言人 · ' + segs.length + ' 段</div>' +
      '<div class="segments">' + segs.map(function (s) {
        var name = (s && (s.speaker || s.speaker_id)) || '未知';
        var at = (s && (s.start_ms || s.start)) || 0;
        var clock = at ? (Math.floor(at / 60000) + ':' + String(Math.floor((at % 60000) / 1000)).padStart(2, '0')) : '';
        return '<div class="seg"><b>' + esc(name) + '</b>' + (clock ? '<i>' + esc(clock) + '</i>' : '') +
          '<span>' + esc((s && (s.text || s.content)) || '') + '</span></div>';
      }).join('') + '</div>';
    openSheet('speakers', '区分发言人', recordLabel(d) + html, { tall: true });
  }

  async function openCapability(cap) {
    if (cap === 'knowledge' || cap === 'live') {
      openSheet('planned', cap === 'knowledge' ? '知识库' : '实时转译',
        '<div class="empty">该能力正在规划中，敬请期待。</div>');
      return;
    }
    if (cap === 'todos') {
      var data = await apiJson('/api/h5/recorder/todos');
      var items = (data && data.items) || [];
      var html = items.length
        ? '<ul class="todo-list">' + items.map(function (t) {
            var meta = [t.owner, t.due].filter(Boolean).join(' · ');
            return '<li><b>' + esc(t.title) + '</b><span>' + esc(t.record_title || '') + (meta ? ' · ' + esc(meta) : '') + '</span></li>';
          }).join('') + '</ul>'
        : '<div class="empty">还没有识别到待办</div>';
      openSheet('todos', '全部待办', html, { tall: true });
      return;
    }
    if (cap === 'summary') { runOnRecord('总结要点', '选择一条要总结的录音', openSummarySheet); return; }
    if (cap === 'transcribe') { runOnRecord('AI 转写', '选择一条要查看逐字稿的录音', openTranscribeSheet); return; }
    if (cap === 'speakers') { runOnRecord('区分发言人', '选择一条要按说话人查看的录音', openSpeakersSheet); return; }
    if (cap === 'translate') { runOnRecord('翻译', '先选择录音，再选择目标语言', openTranslateSheet); return; }
    if (cap === 'outline') { runOnRecord('思维导图', '选择一条要查看大纲的录音', openOutlineSheet); return; }
    if (cap === 'ask') { runOnRecord('AI 智能体', '选择一条要提问的录音', openAskSheet); return; }
    if (cap === 'export') { runOnRecord('内容导出', '选择一条要导出的录音', openExportSheet); return; }
    runOnRecord('录音详情', '选择一条录音', function (d) { openDetail(d.id); });
  }

  function schedulePoll() {
    if (state.polling) { clearTimeout(state.polling); state.polling = 0; }
    var busy = state.items.some(function (x) {
      return x.status === 'processing' || x.status === 'pending' || x.status === 'queued';
    });
    if (!busy) return;
    state.polling = setTimeout(function () { loadRecords().catch(function () {}); }, 5000);
  }

  /* ---------- recorder ---------- */
  function pickMime() {
    var list = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'];
    for (var i = 0; i < list.length; i++) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(list[i])) return list[i];
    }
    return '';
  }

  async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { alert('当前浏览器不支持录音'); return; }
    var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    var mime = pickMime();
    var rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    state.chunks = [];
    state.startedAt = Date.now();
    rec.ondataavailable = function (e) { if (e.data && e.data.size) state.chunks.push(e.data); };
    rec.onstop = function () {
      stream.getTracks().forEach(function (t) { t.stop(); });
      var type = rec.mimeType || mime || 'audio/webm';
      var ext = type.indexOf('mp4') >= 0 ? '.m4a' : (type.indexOf('ogg') >= 0 ? '.ogg' : '.webm');
      var blob = new Blob(state.chunks, { type: type });
      var stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
      uploadBlob(blob, 'note-' + stamp + ext).catch(function (e) { toast(e.message || '上传失败'); });
    };
    state.media = rec;
    rec.start();
    $('recordBtn').classList.add('is-recording');
    $('recStatus').textContent = '正在录音…';
    $('recTimer').textContent = '00:00';
    state.tickId = setInterval(function () {
      var s = Math.floor((Date.now() - state.startedAt) / 1000);
      $('recTimer').textContent = String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
    }, 250);
  }

  function stopRecording() {
    if (state.media && state.media.state !== 'inactive') state.media.stop();
    state.media = null;
    clearInterval(state.tickId);
    state.tickId = 0;
    $('recordBtn').classList.remove('is-recording');
    $('recStatus').textContent = '正在上传…';
    $('recTimer').textContent = '00:00';
  }

  function showUploadToast(name) {
    var box = $('uploadToast');
    if (!box) return;
    box.classList.remove('hidden', 'err');
    $('uploadTitle').textContent = '正在上传 ' + name;
    $('uploadBar').style.width = '0%';
    $('uploadSub').textContent = '0%';
  }

  function setUploadPct(pct) {
    var p = Math.max(0, Math.min(100, Math.round(pct || 0)));
    var bar = $('uploadBar');
    if (bar) bar.style.width = p + '%';
    var sub = $('uploadSub');
    if (sub) sub.textContent = p + '%';
  }

  function finishUploadToast(title, sub, isErr, hideAfter) {
    var box = $('uploadToast');
    if (!box) return;
    box.classList.toggle('err', !!isErr);
    $('uploadTitle').textContent = title;
    $('uploadSub').textContent = sub || '';
    $('uploadBar').style.width = '100%';
    if (hideAfter) setTimeout(function () { box.classList.add('hidden'); }, hideAfter);
  }

  function insertRecord(rec) {
    if (!rec) return;
    state.items = [rec].concat(state.items.filter(function (x) { return String(x.id) !== String(rec.id); }));
    state.total = Math.max(Number(state.total) || 0, state.items.length);
    state.statuses[String(rec.id)] = rec.status;
    setTab('records');
    renderAll();
  }

  function uploadBlob(blob, name, retried) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      fd.append('file', new File([blob], name, { type: blob.type || 'audio/webm' }));
      fd.append('source_type', 'device');
      fd.append('device_name', '必火听记录');
      showUploadToast(name);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/h5/recorder/files');
      var hs = headers();
      Object.keys(hs).forEach(function (k) { xhr.setRequestHeader(k, hs[k]); });
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) setUploadPct(e.loaded / e.total * 100);
      };
      xhr.onload = function () {
        var d = null;
        try { d = JSON.parse(xhr.responseText || '{}'); } catch (e) { d = null; }
        if (xhr.status === 401 && !retried) {
          renew().then(function (ok) {
            if (ok) resolve(uploadBlob(blob, name, true));
            else reject(new Error('登录状态已失效，请重新登录'));
          }).catch(reject);
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          var msg = friendlyError((d && d.detail) || ('上传失败 ' + xhr.status));
          finishUploadToast('上传失败', msg, true, 5000);
          reject(new Error(msg));
          return;
        }
        setUploadPct(100);
        insertRecord(d && d.record);
        schedulePoll();
        finishUploadToast('上传完成', '已加入录音记录，AI 正在整理…', false, 3200);
        $('recStatus').textContent = '已上传，AI 秘书正在整理…';
        setTimeout(function () { $('recStatus').textContent = '点击麦克风开始录音'; }, 2200);
        resolve(d);
      };
      xhr.onerror = function () {
        var msg = '上传失败，请检查网络后重试';
        finishUploadToast('上传失败', msg, true, 5000);
        reject(new Error(msg));
      };
      xhr.send(fd);
    });
  }

  /* ---------- wire ---------- */
  document.addEventListener('DOMContentLoaded', function () {
    $('loginBtn').addEventListener('click', doLogin);
    $('password').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    $('logoutBtn').addEventListener('click', function () { clearToken(); location.reload(); });
    $('refreshBtn').addEventListener('click', function () { loadRecords().catch(function (e) { toast(e.message); }); });
    $('refreshMine').addEventListener('click', function () { loadRecords().then(renderMine).catch(function (e) { toast(e.message); }); });
    $('recordBtn').addEventListener('click', function () {
      if (state.media && state.media.state === 'recording') stopRecording();
      else startRecording().catch(function (e) { toast(e.message || '无法录音'); });
    });
    ['fileInput', 'fileInputMine'].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.addEventListener('change', function (e) {
        var f = e.target.files && e.target.files[0];
        if (!f) return;
        uploadBlob(f, f.name).catch(function (err) { toast(err.message || '上传失败'); });
        e.target.value = '';
      });
    });
    $('searchInput').addEventListener('input', function (e) { state.query = e.target.value || ''; renderRecords(); });
    $('statusChips').addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('button[data-status]') : null;
      if (!btn) return;
      state.statusFilter = btn.getAttribute('data-status') || '';
      Array.prototype.forEach.call(document.querySelectorAll('#statusChips .chip-btn'), function (b) {
        b.classList.toggle('active', b === btn);
      });
      renderRecords();
    });
    $('tabbar').addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('button[data-tab]') : null;
      if (btn) setTab(btn.getAttribute('data-tab'));
    });
    document.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('[data-tab]') : null;
      if (!btn || btn.closest('#tabbar')) return;
      setTab(btn.getAttribute('data-tab'));
    });

    document.addEventListener('click', function (e) {
      var closer = e.target.closest ? e.target.closest('[data-close]') : null;
      if (closer) { closeSheet(closer.getAttribute('data-close')); return; }
      var cap = e.target.closest ? e.target.closest('[data-cap]') : null;
      if (cap && !cap.disabled) { openCapability(cap.getAttribute('data-cap')).catch(function (err) { toast(err.message); }); return; }
      var emptyAct = e.target.closest ? e.target.closest('[data-empty-action]') : null;
      if (emptyAct) {
        var action = emptyAct.getAttribute('data-empty-action');
        closeSheet('empty');
        if (action === 'record') { setTab('home'); }
        else if (action === 'upload') { var fi = $('fileInput'); if (fi) fi.click(); }
        return;
      }
      var pick = e.target.closest ? e.target.closest('[data-pick-id]') : null;
      if (pick) {
        var pickId = pick.getAttribute('data-pick-id');
        var pickRun = state.pick;
        state.pick = null;
        closeSheet('picker');
        var pickItem = findItem(pickId);
        if (pickRun && pickItem) {
          Promise.resolve(pickRun(pickItem)).catch(function (err) { toast(err.message); });
        }
        return;
      }
      var dAct = e.target.closest ? e.target.closest('[data-detail-act]') : null;
      if (dAct) { handleDetailAct(dAct.getAttribute('data-detail-act')); return; }
      var exp = e.target.closest ? e.target.closest('[data-export]') : null;
      if (exp) {
        var fmt = exp.getAttribute('data-export');
        if (fmt === 'txt' || fmt === 'md') exportDetail(fmt);
        else exportServer(fmt).catch(function (err) { toast(err.message); });
        return;
      }
      var reTr = e.target.closest ? e.target.closest('[data-tr-retranslate]') : null;
      if (reTr) {
        var trBody = sheetBody('translate');
        if (trBody) trBody.innerHTML = '<div class="inline-row"><select id="trLang">' +
          ['English', '日本語', '한국어', 'Русский', 'Español', 'Français', 'Deutsch', 'العربية']
            .map(function (l) { return '<option>' + l + '</option>'; }).join('') +
          '</select><button type="button" data-tr="translate">翻译全文</button></div>';
        return;
      }
      var tr = e.target.closest ? e.target.closest('[data-tr]') : null;
      if (!tr) return;
      var d = state.detail;
      if (!d) return;
      var act = tr.getAttribute('data-tr');
      if (act === 'translate') {
        var lang = (document.getElementById('trLang') || {}).value || 'English';
        tr.disabled = true;
        apiJson('/api/h5/recorder/files/' + d.id + '/translate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_lang: lang })
        }).then(function (r) {
          state.detail = r.record;
          var body = sheetBody('translate');
          if (body) body.innerHTML = '<div class="transcript">' + esc(r.record.translated_text || '') + '</div><div class="kv">目标语言：' + esc(r.record.translate_lang || '') + '</div>';
          refreshBalance();
        }).catch(function (err) { toast(err.message); }).finally(function () { tr.disabled = false; });
      } else if (act === 'ask') {
        var q = (document.getElementById('askInput') || {}).value || '';
        if (!q.trim()) return;
        var ans = document.getElementById('askAnswer');
        if (ans) ans.textContent = '思考中…';
        apiJson('/api/h5/recorder/files/' + d.id + '/ask', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q })
        }).then(function (r) { if (ans) ans.textContent = r.answer || '（没有回答）'; refreshBalance(); })
          .catch(function (err) { if (ans) ans.textContent = err.message; });
      }
    });

    document.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('button[data-act]') : null;
      if (!btn) return;
      var row = btn.closest('.record');
      var it = findItem(row && row.getAttribute('data-id'));
      if (!it) return;
      var act = btn.getAttribute('data-act');
      if (act === 'play') playItem(it).catch(function (err) { toast(err.message); });
      else if (act === 'detail') openDetail(it.id).catch(function (err) { toast(err.message); });
      else if (act === 'retry') apiJson('/api/h5/recorder/files/' + it.id + '/retry', { method: 'POST' }).then(loadRecords).catch(function (err) { toast(err.message); });
      else if (act === 'delete') {
        if (!confirm('删除这条录音？')) return;
        apiJson('/api/h5/recorder/files/' + it.id, { method: 'DELETE' }).then(loadRecords).catch(function (err) { toast(err.message); });
      }
    });

    $('recordBtn') && null;
    setTab('home');
    bootApp();
  });
})();
