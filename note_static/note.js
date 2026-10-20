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
    var me = await apiJson('/auth/me');
    $('whoText').textContent = (me && (me.email || me.phone || me.nickname)) || '';
    return me;
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

  var state = { items: [], media: null, chunks: [], startedAt: 0, tickId: 0, polling: 0 };

  function fmtTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString('zh-CN', { hour12: false });
  }

  function statusChip(s) {
    var map = { processing: '整理中', pending: '排队中', queued: '排队中', completed: '已完成', failed: '失败' };
    var key = map[s] ? s : 'processing';
    return '<span class="chip ' + esc(key) + '">' + esc(map[s] || s || '未知') + '</span>';
  }

  async function loadRecords() {
    var d = await apiJson('/api/h5/recorder/files?page=1&page_size=30');
    state.items = (d && d.items) || [];
    renderRecords();
    schedulePoll();
  }

  function renderRecords() {
    var box = $('records');
    var empty = $('empty');
    if (!state.items.length) { box.innerHTML = ''; empty.classList.remove('hidden'); return; }
    empty.classList.add('hidden');
    box.innerHTML = state.items.map(function (it) {
      return '<div class="record" data-id="' + esc(it.id) + '">' +
        '<div class="meta"><div class="name">' + esc(it.display_name || it.file_name || ('录音 #' + it.id)) + '</div>' +
        '<div class="sub">' + statusChip(it.status) +
        '<span>' + esc(fmtTime(it.recorded_at || it.created_at)) + '</span>' +
        (it.file_size ? '<span>' + Math.round(it.file_size / 1024) + ' KB</span>' : '') +
        (it.error_message ? '<span title="' + esc(it.error_message) + '">' + esc(String(it.error_message).slice(0, 40)) + '</span>' : '') +
        '</div></div>' +
        '<div class="ops">' +
        '<button type="button" data-act="play">播放</button>' +
        '<button type="button" data-act="detail">详情</button>' +
        (it.status === 'failed' ? '<button type="button" data-act="retry">重试</button>' : '') +
        '<button type="button" data-act="delete" class="danger">删除</button>' +
        '</div></div>';
    }).join('');
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
    $('detailTitle').textContent = d.display_name || d.file_name || ('录音 #' + d.id);
    var html = '<div class="kv">' + esc(fmtTime(d.recorded_at || d.created_at)) + (d.source_label ? ' · ' + esc(d.source_label) : '') + '</div>';
    if (d.summary_text) html += '<h4>摘要</h4><div>' + esc(d.summary_text) + '</div>';
    if (Array.isArray(d.key_points) && d.key_points.length) {
      html += '<h4>要点</h4><ul>' + d.key_points.map(function (p) {
        return '<li>' + esc(typeof p === 'string' ? p : JSON.stringify(p)) + '</li>';
      }).join('') + '</ul>';
    }
    if (d.transcript_text) html += '<h4>逐字稿</h4><div class="transcript">' + esc(d.transcript_text) + '</div>';
    if (!d.summary_text && !d.transcript_text) {
      html += '<p class="kv">' + (d.status === 'processing' ? '正在整理，请稍后刷新。' : '暂无整理结果。') + '</p>';
    }
    $('detailBody').innerHTML = html;
    $('detailMask').classList.remove('hidden');
  }

  function schedulePoll() {
    if (state.polling) { clearTimeout(state.polling); state.polling = 0; }
    var busy = state.items.some(function (x) {
      return x.status === 'processing' || x.status === 'pending' || x.status === 'queued';
    });
    if (!busy) return;
    state.polling = setTimeout(function () { loadRecords().catch(function () {}); }, 5000);
  }

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
    setTimeout(function () { $('recStatus').textContent = '点击按钮开始录音'; }, 1800);
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('loginBtn').addEventListener('click', doLogin);
    $('password').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
    $('logoutBtn').addEventListener('click', function () { clearToken(); location.reload(); });
    $('refreshBtn').addEventListener('click', function () { loadRecords().catch(function (e) { alert(e.message); }); });
    $('recordBtn').addEventListener('click', function () {
      if (state.media && state.media.state === 'recording') stopRecording();
      else startRecording().catch(function (e) { alert(e.message || '无法录音'); });
    });
    $('fileInput').addEventListener('change', function (e) {
      var f = e.target.files && e.target.files[0];
      if (!f) return;
      uploadBlob(f, f.name).catch(function (err) { alert(err.message || '上传失败'); });
      e.target.value = '';
    });
    $('detailClose').addEventListener('click', function () { $('detailMask').classList.add('hidden'); });
    $('detailMask').addEventListener('click', function (e) { if (e.target === $('detailMask')) $('detailMask').classList.add('hidden'); });
    $('records').addEventListener('click', function (e) {
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
    bootApp();
  });
})();
