(() => {
  const qs = new URLSearchParams(location.search);
  const brand = (qs.get('brand') || 'bihuo').toLowerCase();
  const $ = (id) => document.getElementById(id);
  const readToken = () => localStorage.getItem(`lobster_h5_token:${brand}`) || localStorage.getItem('lobster_h5_token') || '';
  const toast = (msg) => { const el = $('toast'); if (!el) return; el.textContent = String(msg || '操作失败'); el.classList.add('show'); setTimeout(() => el.classList.remove('show'), 2600); };
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path, options = {}) {
    const token = readToken();
    if (!token) throw new Error('请先登录后再使用文案教练');
    const req = { ...options, headers: { 'X-Lobster-Brand': brand, Authorization: `Bearer ${token}`, ...(options.headers || {}) }, credentials: 'include' };
    if (options.json !== undefined) { req.body = JSON.stringify(options.json); req.headers['Content-Type'] = 'application/json'; delete req.json; }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Number(options.timeoutMs || 240000));
    req.signal = controller.signal;
    let response;
    try { response = await fetch(`${location.origin}${path}${path.includes('?') ? '&' : '?'}brand=${encodeURIComponent(brand)}`, req); }
    catch (e) { throw new Error(e?.name === 'AbortError' ? '请求超时，请检查网络后重试' : (e?.message || '网络请求失败')); }
    finally { clearTimeout(timer); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || `请求失败（${response.status}）`);
    return data;
  }
  const panels = () => [...document.querySelectorAll('[data-panel]')];
  const showPanel = (name) => { panels().forEach(p => p.classList.toggle('hidden', p.dataset.panel !== name)); if (['history','materials','plan'].includes(name)) loadList(name).catch(e => toast(e.message)); window.scrollTo({top:0, behavior:'smooth'}); };
  const snapshot = () => ({ happened: $('happened')?.value.trim() || '', customer_problem: $('problem')?.value.trim() || '', customer_question: $('question')?.value.trim() || '', desired_result: $('desired')?.value.trim() || '', current_change: $('change')?.value.trim() || '', purpose: $('purpose')?.value || '', circle_type: $('circle')?.value || '', image_urls: ($('images')?.value || '').split(/[\n,，；;]/).map(x => x.trim()).filter(Boolean) });
  const fill = (v) => { const map = {happened:'happened',customer_problem:'problem',customer_question:'question',desired_result:'desired',current_change:'change',purpose:'purpose',circle_type:'circle',image_urls:'images'}; Object.entries(map).forEach(([k,id]) => { if ($(id)) $(id).value = Array.isArray(v?.[k]) ? v[k].join('\n') : (v?.[k] || ''); }); };
  const renderResults = (items) => { const box = $('results'); if (!box) return; box.innerHTML = `<div class="result-heading"><div><span class="eyebrow">生成完成</span><h2>选一条最像你会说的话</h2></div><small>发布前请核对素材真实性</small></div><div class="result-grid">${(items || []).map(item => `<article class="result" data-id="${esc(item.record_id)}"><header><span>${esc(item.circle_type || '朋友圈')}</span><b>${esc(item.version_type || '文案')}</b></header><h3>${esc(item.title || '')}</h3><pre>${esc(item.body || '')}</pre><div class="result-note"><b>配图建议</b><p>${esc(item.image_suggestion || '按内容选择真实场景图片')}</p><b>衔接建议</b><p>${esc(item.transition || '结合上一条内容自然发布')}</p></div><label class="confirm"><input type="checkbox" data-confirm>我已核对素材，确认发布</label><button type="button" data-image>生成配图</button><button type="button" data-publish disabled>选择此版并发布</button></article>`).join('')}</div>`; };
  async function generate() { const data = await api('/api/moments-coach/generate', {method:'POST', json:snapshot(), timeoutMs:300000}); renderResults(data.items); localStorage.setItem('lobster_moments_coach_draft', JSON.stringify(snapshot())); }
  async function generateIdea() { const text = $('ideaInput')?.value.trim(); if (!text) throw new Error('请先写一件今天发生的小事，或选择一个灵感标签'); const payload = {happened:text, purpose:'建立信任', circle_type:'生活圈', image_urls:[]}; fill(payload); $('circle').value='生活圈'; showPanel('write'); const data = await api('/api/moments-coach/generate', {method:'POST', json:payload, timeoutMs:300000}); renderResults(data.items); }
  let historyItems = [], uploadedImageUrl = '';
  async function loadList(type) { const endpoint = type === 'history' ? '/api/moments-coach/history' : type === 'materials' ? '/api/moments-coach/materials' : '/api/moments-coach/plans'; const data = await api(endpoint); const box = $(type === 'plan' ? 'plans' : type); if (!box) return; if (type === 'history') { historyItems = data.items || []; if ($('profileTotal')) $('profileTotal').textContent = historyItems.length; box.innerHTML = historyItems.map(x => `<article class="list-card"><header><span>${esc(x.circle_type || '朋友圈')}</span><span>${esc(String(x.created_at || '').replace('T',' ').slice(0,16))}</span></header><strong>${esc(x.title || '未命名文案')}</strong><pre>${esc(x.body || '')}</pre><button type="button" data-copy="${esc(x.body || '')}">复制文案</button></article>`).join('') || '<div class="list-card">还没有生成过草稿。</div>'; } else if (type === 'materials') { box.innerHTML = (data.items || []).map(x => `<article class="list-card" data-item='${esc(JSON.stringify(x))}'><header><span>真实素材</span><span>${esc(String(x.created_at || '').replace('T',' ').slice(0,16))}</span></header><strong>${esc(x.title || x.happened || '未命名素材')}</strong><pre>${esc(x.happened || x.current_change || '')}</pre><button type="button" data-use>用于写一条</button></article>`).join('') || '<div class="list-card">还没有保存素材。</div>'; } else { box.innerHTML = (data.items || []).map(x => `<article class="list-card"><header><span>已保存排期</span><span>${x.items?.length || 0} 条内容</span></header><strong>${esc(x.name || '朋友圈一周排期')}</strong></article>`).join('') || '<div class="list-card">还没有保存排期。</div>'; } }
  $('backBtn')?.addEventListener('click', () => { const current = panels().find(p => !p.classList.contains('hidden'))?.dataset.panel; if (current && current !== 'home') showPanel('home'); else location.href = `/?brand=${encodeURIComponent(brand)}`; });
  $('guideBtn')?.addEventListener('click', () => $('guide')?.classList.remove('hidden')); $('guideClose')?.addEventListener('click', () => $('guide')?.classList.add('hidden'));
  document.addEventListener('click', async (e) => { const tab=e.target.closest('[data-tab]'); if(tab){e.preventDefault();showPanel(tab.dataset.tab);return;} const go=e.target.closest('[data-go]'); if(go){showPanel(go.dataset.go);return;} const home=e.target.closest('[data-home-action]'); if(home){const a=home.dataset.homeAction; if(a==='image')showPanel('image'); else if(a==='inspire')showPanel('choose'); else if(a==='choose')showPanel('idea'); else if(a==='school')toast('商学院内容即将开放'); return;} const idea=e.target.closest('[data-idea]'); if(idea){$('ideaInput').value=idea.dataset.idea+'：';$('ideaInput').focus();return;} if(e.target.closest('[data-generate-idea]')){try{await generateIdea();}catch(x){toast(x.message);}return;} const circle=e.target.closest('[data-circle]'); if(circle){$('circle').value=circle.dataset.circle;showPanel('write');return;} const use=e.target.closest('[data-use]'); if(use){try{fill(JSON.parse(use.closest('[data-item]').dataset.item));showPanel('write');}catch{x=>toast('素材读取失败');}return;} const copy=e.target.closest('[data-copy]'); if(copy){navigator.clipboard?.writeText(copy.dataset.copy||'').then(()=>toast('文案已复制')).catch(()=>toast('复制失败'));return;} const imageText=e.target.closest('[data-generate-image-text]'); if(imageText){if(!uploadedImageUrl){toast('请先选择图片');return;} try{const data=await api('/api/moments-coach/generate',{method:'POST',json:{happened:$('imagePrompt').value.trim()||'请根据图片生成适合朋友圈的文案',image_urls:[uploadedImageUrl],purpose:'建立信任'},timeoutMs:300000});fill({happened:$('imagePrompt').value.trim(),image_urls:[uploadedImageUrl],purpose:'建立信任'});showPanel('write');renderResults(data.items);}catch(x){toast(x.message);}return;} const result=e.target.closest('.result'); if(result && e.target.closest('[data-image]')){const btn=e.target.closest('[data-image]');btn.disabled=true;try{const d=await api(`/api/moments-coach/${result.dataset.id}/generate-image`,{method:'POST',json:{},timeoutMs:300000});btn.textContent='配图已生成';const img=document.createElement('img');img.src=d.image_url;img.alt='生成配图';img.style='width:100%;margin:10px 0;border-radius:14px';result.insertBefore(img,result.querySelector('.confirm'));}catch(x){btn.disabled=false;toast(x.message);}return;} if(result && e.target.closest('[data-publish]')){if(!result.querySelector('[data-confirm]')?.checked){toast('请先确认素材真实且已人工核对');return;}toast('发布任务已提交');} });
  $('saveMaterial')?.addEventListener('click', async () => {try{await api('/api/moments-coach/materials',{method:'POST',json:snapshot()});toast('素材已保存');}catch(x){toast(x.message);}}); $('saveMaterialTop')?.addEventListener('click', async () => {try{await api('/api/moments-coach/materials',{method:'POST',json:snapshot()});toast('素材已保存');}catch(x){toast(x.message);}}); $('generate')?.addEventListener('click', async () => {try{await generate();}catch(x){toast(x.message);}});
  $('imageFile')?.addEventListener('change', async (e) => { const file=e.target.files?.[0]; if(!file)return; const status=$('imageStatus'), preview=$('imagePreview'); if(preview){preview.src=URL.createObjectURL(file);preview.classList.remove('hidden');} e.target.disabled=true; if(status)status.textContent='正在上传图片…'; try{const fd=new FormData();fd.append('file',file,file.name||'image.jpg');const d=await api('/api/h5-chat/uploads',{method:'POST',body:fd,timeoutMs:60000});uploadedImageUrl=d.url||'';if(!uploadedImageUrl)throw new Error('服务器未返回图片地址');if(status)status.textContent='图片已上传，可以点击生成文案';}catch(x){if(status)status.textContent='上传失败：'+x.message;toast(x.message);}finally{e.target.disabled=false;} });
  // Handle publishing in capture phase so the request is actually queued before the legacy click handler.
  document.addEventListener('click', async (e) => {
    const button = e.target.closest('[data-publish]');
    if (!button) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    const result = button.closest('.result');
    if (!result || !result.querySelector('[data-confirm]')?.checked) { toast('请先确认素材真实且已人工核对'); return; }
    button.disabled = true;
    try {
      const accounts = await api('/api/scheduled-tasks/publish/accounts');
      const list = accounts.accounts || [];
      const account = list.find(x => String(x.platform || '').toLowerCase() === 'wechat_moments') || list[0];
      if (!account) throw new Error('请先绑定微信朋友圈账号');
      await api(`/api/moments-coach/${result.dataset.id}/publish-request`, { method:'POST', json:{ account_id:account.account_id || account.id || '', account_nickname:account.nickname || '', installation_id:account.installation_id || '', image_urls:snapshot().image_urls } });
      toast('发布任务已提交');
    } catch (x) { button.disabled = false; toast(x.message); }
  }, true);
  try{fill(JSON.parse(localStorage.getItem('lobster_moments_coach_draft')||'null')||{});}catch{} showPanel('home');
})();
