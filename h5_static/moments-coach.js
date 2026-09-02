(() => {
  const params = new URLSearchParams(location.search);
  const brand = (params.get('brand') || 'bihuo').toLowerCase();
  const token = localStorage.getItem(`lobster_h5_token:${brand}`) || localStorage.getItem('lobster_h5_token') || '';
  const apiBase = location.hostname === 'localhost' && location.port === '8000' ? 'http://127.0.0.1:8002' : location.origin;
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
  const toast = (msg) => { const el = $('toast'); if (!el) return; el.textContent = msg; el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 2400); };
  async function api(path, options = {}) {
    const headers = { 'X-Lobster-Brand': brand, ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers || {}) };
    const req = { ...options, headers };
    if (options.json) { req.body = JSON.stringify(options.json); req.headers['Content-Type'] = 'application/json'; delete req.json; }
    const r = await fetch(`${apiBase}${path}${path.includes('?') ? '&' : '?'}brand=${encodeURIComponent(brand)}`, req);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || data.message || '请求失败');
    return data;
  }
  const snapshot = () => ({
    happened: $('happened')?.value.trim() || '', customer_problem: $('problem')?.value.trim() || '',
    customer_question: $('question')?.value.trim() || '', desired_result: $('desired')?.value.trim() || '',
    current_change: $('change')?.value.trim() || '', purpose: $('purpose')?.value || '', circle_type: $('circle')?.value || '',
    image_urls: ($('images')?.value || '').split(/[\n,，；;]/).map(v => v.trim()).filter(Boolean)
  });
  const fill = (v = {}) => {
    const map = { happened:'happened', customer_problem:'problem', customer_question:'question', desired_result:'desired', current_change:'change', purpose:'purpose', circle_type:'circle', image_urls:'images' };
    Object.entries(map).forEach(([k, id]) => { if ($(id)) $(id).value = Array.isArray(v[k]) ? v[k].join('\n') : (v[k] || ''); });
  };
  const panels = () => [...document.querySelectorAll('[data-panel]')];
  const showPanel = (name) => {
    panels().forEach(p => p.classList.toggle('hidden', p.dataset.panel !== name));
    document.querySelectorAll('[data-tab]').forEach(x => x.classList.toggle('active', x.dataset.tab === name));
    if (['history', 'materials', 'plan'].includes(name)) loadList(name).catch(e => toast(e.message));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  async function generate() {
    const data = await api('/api/moments-coach/generate', { method:'POST', json:snapshot() });
    const box = $('results');
    box.innerHTML = `<div class="result-heading"><div><span class="eyebrow">生成完成</span><h2>选一条最像你会说的话</h2></div><small>发布前请核对素材真实性</small></div><div class="result-grid">${(data.items || []).map(item => `<article class="result" data-id="${esc(item.record_id)}"><header><span>${esc(item.circle_type || '朋友圈')}</span><b>${esc(item.version_type || '文案')}</b></header><h3>${esc(item.title || '')}</h3><pre>${esc(item.body || '')}</pre><div class="result-note"><b>配图建议</b>${esc(item.image_suggestion || '按内容选择真实场景图片')}<b>衔接建议</b>${esc(item.transition || '结合上一条内容自然发布')}</div><label class="confirm"><input type="checkbox">我已核对素材，确认发布</label><button data-image>生成配图</button><button data-publish disabled>选择此版并发布</button></article>`).join('')}</div>`;
    localStorage.setItem('lobster_moments_coach_draft', JSON.stringify(snapshot()));
  }
  async function loadList(type) {
    const endpoint = type === 'history' ? '/api/moments-coach/history' : type === 'materials' ? '/api/moments-coach/materials' : '/api/moments-coach/plans';
    const data = await api(endpoint); const box = $(type); if (!box) return;
    if (type === 'plans') { box.innerHTML = (data.items || []).map(x => `<article class="list-card"><header><span>已保存排期</span><span>${x.items?.length || 0} 条内容</span></header><strong>${esc(x.name || '朋友圈一周排期')}</strong><p>${(x.items || []).map(i => esc(i.circle_type || '朋友圈')).join(' · ')}</p></article>`).join('') || '<div class="list-card">还没有保存排期。</div>'; return; }
    box.innerHTML = (data.items || []).map(x => `<article class="list-card" data-item='${esc(JSON.stringify(x))}'><header><span>${type === 'history' ? esc(x.circle_type || '朋友圈') : '真实素材'}</span><span>${esc(String(x.created_at || '').replace('T', ' ').slice(0, 16))}</span></header><strong>${esc(x.title || x.happened?.slice(0, 28) || x.version_type || '未命名')}</strong><pre>${esc(x.body || x.happened || x.current_change || x.customer_problem || '')}</pre>${type === 'materials' ? '<button data-use>用于写一条</button>' : ''}</article>`).join('') || `<div class="list-card">${type === 'history' ? '还没有生成过草稿。' : '还没有素材。'}</div>`;
  }
  $('backBtn').onclick = () => { const active = panels().find(p => !p.classList.contains('hidden'))?.dataset.panel; if (active && active !== 'home') showPanel('home'); else location.href = `/?brand=${encodeURIComponent(brand)}`; };
  $('guideBtn').onclick = () => $('guide')?.classList.remove('hidden');
  $('guideClose').onclick = () => { $('guide')?.classList.add('hidden'); localStorage.setItem('lobster_moments_coach_guide_dismissed', '1'); };
  document.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-tab]'); if (tab) { e.preventDefault(); showPanel(tab.dataset.tab); return; }
    const go = e.target.closest('[data-go]'); if (go) { e.preventDefault(); showPanel(go.dataset.go); return; }
    const home = e.target.closest('[data-home-action]'); if (home) { const action = home.dataset.homeAction; if (action === 'school') return toast('商学院内容即将开放'); if (action === 'image') { showPanel('write'); $('images')?.focus(); } else if (action === 'inspire') showPanel('write'); else showPanel('choose'); return; }
    const circle = e.target.closest('[data-circle]'); if (circle) { const value = circle.dataset.circle; if ($('circle')) $('circle').value = value; showPanel('write'); const title = document.querySelector('.intro h2'); if (title) title.textContent = `${value}，今天想写点什么？`; return; }
    const use = e.target.closest('[data-use]'); if (use) { try { fill(JSON.parse(use.closest('[data-item]').dataset.item)); showPanel('write'); toast('素材已带入写作区'); } catch (_) { toast('素材读取失败'); } }
  });
  const saveMaterial = () => api('/api/moments-coach/materials', { method:'POST', json:snapshot() }).then(() => toast('素材已保存')).catch(e => toast(e.message));
  $('saveMaterial')?.addEventListener('click', saveMaterial); $('saveMaterialTop')?.addEventListener('click', saveMaterial); $('generate')?.addEventListener('click', () => generate().catch(e => toast(e.message)));
  $('savePlan')?.addEventListener('click', () => { const items = [...document.querySelectorAll('.result')].map((el, i) => ({ draft_record_id: el.dataset.id, circle_type: el.querySelector('header span')?.textContent || '生活圈', sort_order: i })); api('/api/moments-coach/plans', { method:'POST', json:{ name:'朋友圈一周排期', items } }).then(() => toast('排期已保存')).catch(e => toast(e.message)); });
  $('results')?.addEventListener('change', (e) => { if (e.target.matches('input[type=checkbox]')) e.target.closest('.result').querySelector('[data-publish]').disabled = !e.target.checked; });
  $('results')?.addEventListener('click', async (e) => {
    const result = e.target.closest('.result'); if (!result) return;
    if (e.target.closest('[data-image]')) { const btn = e.target.closest('[data-image]'); btn.disabled = true; try { const d = await api(`/api/moments-coach/${result.dataset.id}/generate-image`, { method:'POST', json:{} }); btn.textContent = '配图已生成'; const img = document.createElement('img'); img.src = d.image_url; img.alt = '生成配图'; img.style = 'width:100%;margin:10px 0;border-radius:14px'; result.insertBefore(img, result.querySelector('.confirm')); } catch (err) { toast(err.message); btn.disabled = false; } }
    if (e.target.closest('[data-publish]')) { const c = result.querySelector('input[type=checkbox]'); if (!c.checked) return toast('请先确认素材真实且已人工核对'); try { const accounts = await api('/api/scheduled-tasks/publish/accounts'); const account = (accounts.accounts || []).find(x => String(x.platform || '').toLowerCase() === 'wechat_moments') || (accounts.accounts || [])[0]; if (!account) throw new Error('请先绑定微信朋友圈账号'); await api(`/api/moments-coach/${result.dataset.id}/publish-request`, { method:'POST', json:{ account_id:account.account_id || account.id || '', account_nickname:account.nickname || '', installation_id:account.installation_id || '', image_urls:snapshot().image_urls } }); toast('发布任务已提交'); } catch (err) { toast(err.message); } }
  });
  try { fill(JSON.parse(localStorage.getItem('lobster_moments_coach_draft') || 'null') || {}); } catch (_) {}
  if (localStorage.getItem('lobster_moments_coach_guide_dismissed') !== '1') $('guide')?.classList.remove('hidden');
  showPanel('home');
})();
