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

  function headers(extra) {
    var h = Object.assign({ 'X-Lobster-Brand': BRAND }, extra || {});
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
        headers: { 'Authorization': 'Bearer ' + t, 'X-Lobster-Brand': BRAND, 'Content-Type': 'application/json' },
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
    if (!r.ok) throw Object.assign(new Error((d && (d.detail || d.msg)) || ('请求失败 ' + r.status)), { status: r.status, data: d });
    return d;
  }

  var state = {
    items: [], total: 0, me: null, tab: 'home', query: '', statusFilter: '',
    media: null, chunks: [], startedAt: 0, tickId: 0, polling: 0
  };

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

  async function loadMe() {
    state.me = await apiJson('/auth/me');
    var name = (state.me && (state.me.email || state.me.phone || state.me.nickname)) || '';
    $('whoText').textContent = name;
    $('mineName').textContent = name || '未登录';
    $('mineBrand').textContent = '品牌：' + BRAND;
    $('mineAvatar').textContent = (name || '必').slice(0, 1).toUpperCase();
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
    if (tab === 'mine') renderMine();
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
    return '<div class="record" data-id="' + esc(it.id) + '">' +
      '<div class="meta"><div class="name">' + esc(it.display_name || it.file_name || ('录音 #' + it.id)) + '</div>' +
      '<div class="sub">' + statusChip(it.status) +
      '<span>' + esc(fmtTime(it.recorded_at || it.created_at)) + '</span>' +
      (it.file_size ? '<span>' + Math.round(it.file_size / 1024) + ' KB</span>' : '') +
      '</div></div>' +
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

  async function showDetail(it) {
    var d = await apiJson('/api/h5/recorder/files/' + encodeURIComponent(it.id));
    state.detail = d;
    $('detailTitle').textContent = d.display_name || d.file_name || ('录音 #' + d.id);
    var html = '<div class="kv">' + esc(fmtTime(d.recorded_at || d.created_at)) + (d.source_label ? ' · ' + esc(d.source_label) : '') + '</div>';
    if (d.summary_text) html += '<h4>摘要</h4><div>' + esc(d.summary_text) + '</div>';
    if (Array.isArray(d.key_points) && d.key_points.length) {
      html += '<h4>要点</h4><ul>' + d.key_points.map(function (p) {
        return '<li>' + esc(typeof p === 'string' ? p : JSON.stringify(p)) + '</li>';
      }).join('') + '</ul>';
    }
    if (Array.isArray(d.segments) && d.segments.length) {
      html += '<h4>发言人逐字稿</h4><div class="segments">' + d.segments.map(function (s) {
        var who = s && (s.speaker || s.speaker_id || '');
        var text = s && (s.text || s.content || '');
        var at = s && (s.start || s.start_time || s.begin || 0);
        return '<div class="seg"><b>' + esc(who || '未知') + '</b>' + (at ? '<i>' + esc(String(at)) + '</i>' : '') + '<span>' + esc(text) + '</span></div>';
      }).join('') + '</div>';
    } else if (d.transcript_text) {
      html += '<h4>逐字稿</h4><div class="transcript">' + esc(d.transcript_text) + '</div>';
    }
    html += '<div class="detail-actions"><button type="button" data-export="txt">导出 TXT</button><button type="button" data-export="md">导出 Markdown</button></div>';
    if (!d.summary_text && !d.transcript_text && !(d.segments || []).length) {
      html += '<p class="kv">' + (d.status === 'processing' ? '正在整理，请稍后刷新。' : '暂无整理结果。') + '</p>';
    }
    $('detailBody').innerHTML = html;
    $('detailMask').classList.remove('hidden');
  }

  function detailText(d, markdown) {
    var lines = [];
    var title = d.display_name || d.file_name || ('录音 #' + d.id);
    if (markdown) {
      lines.push('# ' + title, '', '- 时间：' + fmtTime(d.recorded_at || d.created_at), '- 来源：' + (d.source_label || ''), '');
      if (d.summary_text) lines.push('## 摘要', '', d.summary_text, '');
      if (Array.isArray(d.key_points) && d.key_points.length) {
        lines.push('## 要点', '');
        d.key_points.forEach(function (p) { lines.push('- ' + (typeof p === 'string' ? p : JSON.stringify(p))); });
        lines.push('');
      }
      if (Array.isArray(d.segments) && d.segments.length) {
        lines.push('## 逐字稿', '');
        d.segments.forEach(function (s) { lines.push('**' + (s.speaker || s.speaker_id || '未知') + '**：' + (s.text || s.content || '')); });
        lines.push('');
      } else if (d.transcript_text) {
        lines.push('## 逐字稿', '', d.transcript_text, '');
      }
    } else {
      lines.push(title);
      lines.push('时间：' + fmtTime(d.recorded_at || d.created_at));
      lines.push('来源：' + (d.source_label || ''));
      if (d.summary_text) lines.push('', '【摘要】', d.summary_text);
      if (Array.isArray(d.key_points) && d.key_points.length) {
        lines.push('', '【要点】');
        d.key_points.forEach(function (p) { lines.push('- ' + (typeof p === 'string' ? p : JSON.stringify(p))); });
      }
      if (Array.isArray(d.segments) && d.segments.length) {
        lines.push('', '【逐字稿】');
        d.segments.forEach(function (s) { lines.push('[' + (s.speaker || s.speaker_id || '未知') + '] ' + (s.text || s.content || '')); });
      } else if (d.transcript_text) {
        lines.push('', '【逐字稿】', d.transcript_text);
      }
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
      uploadBlob(blob, 'note-' + stamp + ext).catch(function (e) { alert(e.message || '上传失败'); });
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

  async function uploadBlob(blob, name) {
    var fd = new FormData();
    fd.append('file', new File([blob], name, { type: blob.type || 'audio/webm' }));
    fd.append('source_type', 'device');
    fd.append('device_name', '必火听记录');
    var r = await api('/api/h5/recorder/files', { method: 'POST', body: fd });
    if (!r.ok) {
      var d = null;
      try { d = await r.json(); } catch (e) { d = null; }
      throw new Error((d && d.detail) || '上传失败');
    }
    $('recStatus').textContent = '已上传，AI 秘书正在整理…';
    await loadRecords();
    setTimeout(function () { $('recStatus').textContent = '点击麦克风开始录音'; }, 1800);
  }

  /* ---------- wire ---------- */
  document.addEventListener('DOMContentLoaded', function () {
    $('loginBtn').addEventListener('click', doLogin);
    $('password').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    $('logoutBtn').addEventListener('click', function () { clearToken(); location.reload(); });
    $('refreshBtn').addEventListener('click', function () { loadRecords().catch(function (e) { alert(e.message); }); });
    $('refreshMine').addEventListener('click', function () { loadRecords().then(renderMine).catch(function (e) { alert(e.message); }); });
    $('recordBtn').addEventListener('click', function () {
      if (state.media && state.media.state === 'recording') stopRecording();
      else startRecording().catch(function (e) { alert(e.message || '无法录音'); });
    });
    ['fileInput', 'fileInputMine'].forEach(function (id) {
      var el = $(id);
      if (!el) return;
      el.addEventListener('change', function (e) {
        var f = e.target.files && e.target.files[0];
        if (!f) return;
        uploadBlob(f, f.name).catch(function (err) { alert(err.message || '上传失败'); });
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
    $('detailClose').addEventListener('click', function () { $('detailMask').classList.add('hidden'); });
    document.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-export]') : null;
      if (!b) return;
      exportDetail(b.getAttribute('data-export'));
    });
    document.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('[data-cap]') : null;
      if (!b || b.disabled) return;
      var latest = state.items[0];
      if (!latest) { alert('还没有录音，先录一条吧'); return; }
      showDetail(latest).catch(function (err) { alert(err.message); });
    });
    $('detailMask').addEventListener('click', function (e) { if (e.target === $('detailMask')) $('detailMask').classList.add('hidden'); });
    document.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('button[data-act]') : null;
      if (!btn) return;
      var row = btn.closest('.record');
      var it = findItem(row && row.getAttribute('data-id'));
      if (!it) return;
      var act = btn.getAttribute('data-act');
      if (act === 'play') playItem(it).catch(function (err) { alert(err.message); });
      else if (act === 'detail') showDetail(it).catch(function (err) { alert(err.message); });
      else if (act === 'retry') apiJson('/api/h5/recorder/files/' + it.id + '/retry', { method: 'POST' }).then(loadRecords).catch(function (err) { alert(err.message); });
      else if (act === 'delete') {
        if (!confirm('删除这条录音？')) return;
        apiJson('/api/h5/recorder/files/' + it.id, { method: 'DELETE' }).then(loadRecords).catch(function (err) { alert(err.message); });
      }
    });
    setTab('home');
    bootApp();
  });
})();
