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
  let panelHistory = [];
  const currentPanel = () => panels().find(p => !p.classList.contains('hidden'))?.dataset.panel || 'home';
  const showPanel = (name, options = {}) => { const current = currentPanel(); if (options.push && current !== name) panelHistory.push(current); if (name === 'home' && options.reset !== false) panelHistory = []; panels().forEach(p => p.classList.toggle('hidden', p.dataset.panel !== name)); if (['history','materials','plan'].includes(name)) loadList(name).catch(e => toast(e.message)); window.scrollTo({top:0, behavior:'smooth'}); };
  const goBackPanel = () => { const current = currentPanel(); const previous = panelHistory.pop(); if (previous) { showPanel(previous, {push:false, reset:false}); return; } if (current !== 'home') { showPanel('home', {push:false}); return; } location.href = `/?brand=${encodeURIComponent(brand)}`; };
  const CIRCLE_UI = {
    '': { eyebrow:'从真实素材出发', title:'今天，想让谁记住什么？', description:'把发生的事、对方的顾虑或一个变化告诉我。教练会判断内容节奏，并给你三个可以直接修改的版本。', visible:['happened','problem','question','desired','change'], labels:{happened:'今天发生了什么',problem:'她之前卡在哪里',question:'她问过什么',desired:'她真正想要的结果',change:'现在有什么进展'}, placeholders:{happened:'例如：一位客户把拖了很久的方案重新拿出来聊……',problem:'客户原来的困扰',question:'保留真实问法',desired:'她希望发生的变化',change:'真实变化、反馈或阶段结果'}},
    '生活圈': { eyebrow:'生活圈', title:'生活圈', description:'用你的日常，将你打造成为朋友圈之星。', visible:['happened'], labels:{happened:'谁做了什么事'}, placeholders:{happened:'比如：下午陪我儿子骑自行车，他骑得很慢，我在后面跟着，突然觉得很放松'}},
    '咨询圈': { eyebrow:'咨询圈', title:'咨询圈', description:'消除客户疑问，让客户自动下单。', visible:['question','problem'], labels:{question:'客户问了什么问题',problem:'你的判断或建议'}, placeholders:{question:'比如：花了10多万学习，短视频、直播、商业模式都学了，收入还是没提升',problem:'比如：文案是线上成交的基础'}},
    '反馈圈': { eyebrow:'反馈圈', title:'反馈圈', description:'用一个客户反馈，刺激井喷式订单。', visible:['problem','desired','change'], labels:{problem:'客户之前什么状态',desired:'报名的课程 / 使用的产品',change:'现在有什么变化'}, placeholders:{problem:'比如：花了10万各种报课赚不到钱',desired:'比如：报名我的文案私教',change:'比如：一场发售收了20万'}},
    '收款圈': { eyebrow:'收款圈', title:'收款圈', description:'用一个收款，引爆收款。', visible:['problem','desired','change'], labels:{problem:'客户情况',desired:'报名的课程 / 购买的产品',change:'我能带他拿到的结果'}, placeholders:{problem:'比如：花了10多万学习，收入没有提升，越来越焦虑',desired:'比如：我的文案私教',change:'比如：带他把能力变成钱，有钱有闲又有爱'}},
    '促成交圈': { eyebrow:'促成交圈', title:'促成交', description:'让用户忍不住立刻给你付钱。', visible:['desired','problem','change'], labels:{desired:'用户想获得什么结果',problem:'产品和优惠',change:'截止或名额'}, placeholders:{desired:'比如：让孩子愿意沟通，家庭氛围变轻松',problem:'比如：亲子沟通陪跑营，原价1999，今天699，送1次诊断',change:'比如：今晚12点截止，只开放20个名额'}}
  };
  const updateCircleUI = (value) => { const cfg = CIRCLE_UI[value] || CIRCLE_UI['']; const eyebrow=$('circleEyebrow'), title=$('circleTitle'), desc=$('circleDescription'); if(eyebrow) eyebrow.textContent=cfg.eyebrow; if(title) title.textContent=cfg.title; if(desc) desc.textContent=cfg.description; document.querySelectorAll('[data-circle-field]').forEach((el)=>{ const key=el.dataset.circleField; const show=cfg.visible.includes(key); el.classList.toggle('circle-field-hidden', !show); const label=el.querySelector('.field-label'); const input=el.querySelector('textarea'); if(label) label.textContent=cfg.labels[key] || CIRCLE_UI[''].labels[key] || label.textContent; if(input && cfg.placeholders[key]) input.placeholder=cfg.placeholders[key]; }); const optional=document.querySelector('[data-circle-optional]'); if(optional) optional.classList.toggle('circle-field-hidden', !!value); const direction=$('circleDirection'); if(direction) direction.classList.toggle('circle-field-hidden', !!value); const card=document.querySelector('.compose-card'); if(card) card.classList.toggle('circle-specific', !!value); };
  const snapshot = () => { const circle = $('circle')?.value || ''; const visible = (CIRCLE_UI[circle] || CIRCLE_UI['']).visible; return { happened: visible.includes('happened') ? ($('happened')?.value.trim() || '') : '', customer_problem: visible.includes('problem') ? ($('problem')?.value.trim() || '') : '', customer_question: visible.includes('question') ? ($('question')?.value.trim() || '') : '', desired_result: visible.includes('desired') ? ($('desired')?.value.trim() || '') : '', current_change: visible.includes('change') ? ($('change')?.value.trim() || '') : '', purpose: $('purpose')?.value || '', circle_type: circle, image_urls: ($('images')?.value || '').split(/[\n,，；;]/).map(x => x.trim()).filter(Boolean) }; };
  const fill = (v) => { const map = {happened:'happened',customer_problem:'problem',customer_question:'question',desired_result:'desired',current_change:'change',purpose:'purpose',circle_type:'circle',image_urls:'images'}; Object.entries(map).forEach(([k,id]) => { if ($(id)) $(id).value = Array.isArray(v?.[k]) ? v[k].join('\n') : (v?.[k] || ''); }); updateCircleUI(v?.circle_type || $('circle')?.value || ''); };
  const renderResults = (items) => { const box = $('results'); if (!box) return; box.innerHTML = `<div class="result-heading"><div><span class="eyebrow">生成完成</span><h2>选一条最像你会说的话</h2></div><small>发布前请核对素材真实性</small></div><div class="result-grid">${(items || []).map(item => `<article class="result" data-id="${esc(item.record_id)}"><header><span>${esc(item.circle_type || '朋友圈')}</span><b>${esc(item.version_type || '文案')}</b></header><h3>${esc(item.title || '')}</h3><pre>${esc(item.body || '')}</pre><div class="result-note"><b>配图建议</b><p>${esc(item.image_suggestion || '按内容选择真实场景图片')}</p><b>衔接建议</b><p>${esc(item.transition || '结合上一条内容自然发布')}</p></div><label class="confirm"><input type="checkbox" data-confirm>我已核对素材，确认发布</label><button type="button" data-image>生成配图</button><button type="button" data-publish disabled>选择此版并发布</button></article>`).join('')}</div>`; };
  async function requestGeneration(payload) {
    const data = await api('/api/moments-coach/generate', {method:'POST', json:payload, timeoutMs:30000});
    if (!data.job_id) throw new Error('生成任务未创建');
    const box = $('results'); if (box) box.innerHTML = '<div class="result-heading"><h2>正在生成文案…</h2><small>任务已提交，页面不会被长连接占住</small></div>';
    for (let i = 0; i < 200; i++) {
      await new Promise(resolve => setTimeout(resolve, 1500));
      const status = await api(`/api/moments-coach/generate/${encodeURIComponent(data.job_id)}`, {timeoutMs:15000});
      if (status.status === 'completed') { renderResults(status.items || []); return status; }
      if (status.status === 'failed') throw new Error(status.error || '文案生成失败');
    }
    throw new Error('生成等待超时，请到历史记录查看');
  }
  async function generate() { const button = $('generate'); if (button) { button.disabled = true; button.textContent = '生成中…'; } try { const payload = snapshot(); const result = await requestGeneration(payload); localStorage.setItem('lobster_moments_coach_draft', JSON.stringify(payload)); return result; } finally { if (button) { button.disabled = false; button.textContent = '生成文案'; } } }
  const ideaCircle = (text) => { const value = String(text || ''); if (/客户提问|客户顾虑/.test(value)) return '咨询圈'; if (/客户变化/.test(value)) return '反馈圈'; if (/成交收款/.test(value)) return '收款圈'; if (/限时活动/.test(value)) return '促成交圈'; return '生活圈'; };
  async function generateIdea() { const text = $('ideaInput')?.value.trim(); if (!text) throw new Error('请先写一件真实发生的事，或选择一个灵感切口'); const circle = ideaCircle(text); const payload = {happened:text, purpose:circle === '咨询圈' ? '消除疑虑' : circle === '反馈圈' ? '展示变化' : circle === '收款圈' ? '展示变化' : circle === '促成交圈' ? '推动行动' : '建立信任', circle_type:circle, image_urls:[]}; fill(payload); $('circle').value=circle; updateCircleUI(circle); showPanel('write'); await requestGeneration(payload); }
  let historyItems = [], uploadedImageUrl = '';
  async function loadList(type) { const endpoint = type === 'history' ? '/api/moments-coach/history' : type === 'materials' ? '/api/moments-coach/materials' : '/api/moments-coach/plans'; const data = await api(endpoint); const box = $(type === 'plan' ? 'plans' : type); if (!box) return; if (type === 'history') { historyItems = data.items || []; if ($('profileTotal')) $('profileTotal').textContent = historyItems.length; box.innerHTML = historyItems.map(x => `<article class="list-card"><header><span>${esc(x.circle_type || '朋友圈')}</span><span>${esc(String(x.created_at || '').replace('T',' ').slice(0,16))}</span></header><strong>${esc(x.title || '未命名文案')}</strong><pre>${esc(x.body || '')}</pre><button type="button" data-copy="${esc(x.body || '')}">复制文案</button></article>`).join('') || '<div class="list-card">还没有生成过草稿。</div>'; } else if (type === 'materials') { box.innerHTML = (data.items || []).map(x => `<article class="list-card" data-item='${esc(JSON.stringify(x))}'><header><span>真实素材</span><span>${esc(String(x.created_at || '').replace('T',' ').slice(0,16))}</span></header><strong>${esc(x.title || x.happened || '未命名素材')}</strong><pre>${esc(x.happened || x.current_change || '')}</pre><button type="button" data-use>用于写一条</button></article>`).join('') || '<div class="list-card">还没有保存素材。</div>'; } else { box.innerHTML = (data.items || []).map(x => `<article class="list-card"><header><span>已保存排期</span><span>${x.items?.length || 0} 条内容</span></header><strong>${esc(x.name || '朋友圈一周排期')}</strong></article>`).join('') || '<div class="list-card">还没有保存排期。</div>'; } }
  $('backBtn')?.addEventListener('click', goBackPanel);
  $('guideBtn')?.addEventListener('click', () => $('guide')?.classList.remove('hidden')); $('guideClose')?.addEventListener('click', () => $('guide')?.classList.add('hidden'));
  document.addEventListener('change', (e) => { if (e.target.id === 'circle') updateCircleUI(e.target.value); });
  document.addEventListener('click', async (e) => { const tab=e.target.closest('[data-tab]'); if(tab){e.preventDefault();showPanel(tab.dataset.tab);return;} const back=e.target.closest('[data-back-panel]'); if(back){e.preventDefault();goBackPanel();return;} const go=e.target.closest('[data-go]'); if(go){e.preventDefault(); if(go.dataset.go === 'home') { showPanel('home', {push:false}); } else { showPanel(go.dataset.go, {push:false}); } return;} const home=e.target.closest('[data-home-action]'); if(home){const a=home.dataset.homeAction; if(a==='image')showPanel('image',{push:true}); else if(a==='inspire')showPanel('choose',{push:true}); else if(a==='choose')showPanel('idea',{push:true}); else if(a==='school')showPanel('school',{push:true}); return;} const idea=e.target.closest('[data-idea]'); if(idea){$('ideaInput').value=idea.dataset.idea+'：';$('ideaInput').focus();return;} if(e.target.closest('[data-generate-idea]')){try{await generateIdea();}catch(x){toast(x.message);}return;} const circle=e.target.closest('[data-circle]'); if(circle){$('circle').value=circle.dataset.circle;updateCircleUI(circle.dataset.circle);showPanel('write',{push:true});return;} const use=e.target.closest('[data-use]'); if(use){try{fill(JSON.parse(use.closest('[data-item]').dataset.item));showPanel('write',{push:true});}catch{x=>toast('素材读取失败');}return;} const copy=e.target.closest('[data-copy]'); if(copy){navigator.clipboard?.writeText(copy.dataset.copy||'').then(()=>toast('文案已复制')).catch(()=>toast('复制失败'));return;} const imageText=e.target.closest('[data-generate-image-text]'); if(imageText){if(!uploadedImageUrl){toast('请先选择图片');return;} try{const data=await api('/api/moments-coach/generate',{method:'POST',json:{happened:$('imagePrompt').value.trim()||'请根据图片生成适合朋友圈的文案',image_urls:[uploadedImageUrl],purpose:'建立信任'},timeoutMs:300000});fill({happened:$('imagePrompt').value.trim(),image_urls:[uploadedImageUrl],purpose:'建立信任'});showPanel('write',{push:true});renderResults(data.items);}catch(x){toast(x.message);}return;} const result=e.target.closest('.result'); if(result && e.target.closest('[data-image]')){const btn=e.target.closest('[data-image]');btn.disabled=true;try{const d=await api(`/api/moments-coach/${result.dataset.id}/generate-image`,{method:'POST',json:{},timeoutMs:300000});btn.textContent='配图已生成';const img=document.createElement('img');img.src=d.image_url;img.alt='生成配图';img.style='width:100%;margin:10px 0;border-radius:14px';result.insertBefore(img,result.querySelector('.confirm'));}catch(x){btn.disabled=false;toast(x.message);}return;} if(result && e.target.closest('[data-publish]')){if(!result.querySelector('[data-confirm]')?.checked){toast('请先确认素材真实且已人工核对');return;}toast('发布任务已提交');} });
  $('saveMaterial')?.addEventListener('click', async () => {try{await api('/api/moments-coach/materials',{method:'POST',json:snapshot()});toast('素材已保存');}catch(x){toast(x.message);}}); $('saveMaterialTop')?.addEventListener('click', async () => {try{await api('/api/moments-coach/materials',{method:'POST',json:snapshot()});toast('素材已保存');}catch(x){toast(x.message);}}); $('generate')?.addEventListener('click', async () => {try{await generate();}catch(x){toast(x.message);}});
  $('imageFile')?.addEventListener('change', async (e) => { const file=e.target.files?.[0]; if(!file)return; const status=$('imageStatus'), preview=$('imagePreview'); if(preview){preview.src=URL.createObjectURL(file);preview.classList.remove('hidden');} e.target.disabled=true; if(status)status.textContent='正在上传图片…'; try{const fd=new FormData();fd.append('file',file,file.name||'image.jpg');const d=await api('/api/h5-chat/uploads',{method:'POST',body:fd,timeoutMs:60000});uploadedImageUrl=d.url||'';if(!uploadedImageUrl)throw new Error('服务器未返回图片地址');if(status)status.textContent='图片已上传，可以点击生成文案';}catch(x){if(status)status.textContent='上传失败：'+x.message;toast(x.message);}finally{e.target.disabled=false;} });
  // Handle publishing in capture phase so the request is actually queued before the legacy click handler.
  document.addEventListener('click', async (e) => {
    const button = e.target.closest('[data-generate-image-text]');
    if (!button) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (!uploadedImageUrl) { toast('请先选择图片'); return; }
    try {
      const prompt = $('imagePrompt')?.value.trim() || '请根据这张图片生成适合朋友圈的文案';
      const payload = { happened: prompt, purpose: '建立信任', image_urls: [uploadedImageUrl] };
      fill(payload); showPanel('write'); await requestGeneration(payload);
    } catch (x) { toast(x.message); }
  }, true);
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
  try{fill(JSON.parse(localStorage.getItem('lobster_moments_coach_draft')||'null')||{});}catch{} updateCircleUI($('circle')?.value || ''); showPanel('home');
})();
