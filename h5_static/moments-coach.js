(() => {
  const qs = new URLSearchParams(location.search);
  const brand = (qs.get('brand') || 'bihuo').toLowerCase();
  const embedded = qs.get('embedded') === '1';
  const $ = (id) => document.getElementById(id);
  const coachRoot = $('momentsApp');
  const isCurrentCoach = () => document.getElementById('momentsApp') === coachRoot;
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
  const goBackPanel = () => { const current = currentPanel(); const previous = panelHistory.pop(); if (previous) { showPanel(previous, {push:false, reset:false}); return; } if (current !== 'home') { showPanel('home', {push:false}); return; } if (embedded && window.parent !== window) { window.parent.postMessage({ source: 'moments-coach', type: 'moments-coach-back' }, location.origin); return; } location.href = `/?brand=${encodeURIComponent(brand)}`; };
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
  // The buttons are starter material, not labels to be copied into the prompt.
  // Keep the two examples that are explicitly provided by the handoff document verbatim;
  // the other entries are concrete, clearly marked examples that users can replace.
  const IDEA_PROMPTS = {
    life: { circle: '生活圈', text: '今天下午陪我儿子骑自行车，他骑得很慢，我在后面跟着。原本还在赶时间，后来发现孩子回头冲我笑，那一刻我把手机收起来，陪他把这一圈骑完。' },
    question: { circle: '咨询圈', text: '有客户问我：花了10多万学习，短视频、直播、商业模式都学了，收入还是没有提升，问题到底出在哪？我把她的情况拆开后发现，先要解决线上成交的基础，而不是继续堆课程。' },
    change: { circle: '反馈圈', text: '我有个客户以前报课没效果，现在一场发售收了20万，帮我写一条。' },
    payment: { circle: '收款圈', text: '今天完成一笔真实收款：客户之前反复比较方案，确认服务能解决她当前的问题后，决定先从一次小范围陪跑开始。' },
    offer: { circle: '促成交圈', text: '亲子沟通陪跑营原价1999，今天报名699，送1次沟通诊断，今晚12点截止，只开放20个名额。' },
    concern: { circle: '咨询圈', text: '客户在购买前最担心的是：自己已经学过很多方法，付费后仍然不知道怎么落地。我准备把服务步骤、陪跑方式和可交付结果讲清楚，再邀请她判断是否适合。' },
    plan: { circle: '生活圈', text: '我做高端农产品礼盒，想按五种圈型排一周朋友圈：穿插生活、咨询、反馈、收款和促成交内容，避免每天都像在发广告。' },
    image: { circle: '生活圈', text: '我手上有一张真实图片，画面中的人物、地点和当时发生的事需要补充，请根据这些信息写一条朋友圈。' }
  };
  const ideaCircle = (text) => { const value = String(text || ''); if (/客户问|客户顾虑|担心|问题/.test(value)) return '咨询圈'; if (/客户变化|使用前后|结果/.test(value)) return '反馈圈'; if (/成交|收款|付钱/.test(value)) return '收款圈'; if (/优惠|截止|名额|报名/.test(value)) return '促成交圈'; return '生活圈'; };
  const SCHOOL_ARTICLES = [
    {id:'mutual-2',category:'practice',label:'实战干货',date:'08/28',title:'心理按钮·引发互惠②：如何让用户不付钱都觉得不好意思？',body:['真正的互惠不是让用户有压力，而是先让对方感受到你已经认真提供了帮助。先给一条能解决当下问题的建议，再邀请对方判断是否需要继续。','例如客户还在比较方案时，可以把她的情况拆成两步：先指出目前卡点，再说明如果要继续推进，需要补齐什么。对方感受到你的投入，后面的沟通会更自然。','发布时只使用真实咨询记录，隐去姓名、头像和金额；不要把一次正常交流包装成成交结果。'],quote:'先提供清晰价值，再让对方自己决定是否继续。'},
    {id:'authority-3',category:'practice',label:'实战干货',date:'08/28',title:'心理按钮·展示权威③：如何用成就、专业和案例让客户主动尊重你？',body:['权威感不是把头衔堆在一起，而是让客户看见你如何处理具体问题。选择一个真实案例，说明当时的判断、执行和复盘，客户就能理解你的专业边界。','写作时把“我很专业”换成可验证的细节：客户遇到什么情况，你先排除了什么，最后采取了哪一步。没有完整结果时，就写阶段进展，不补写不存在的结果。'],quote:'用过程证明能力，用细节代替自夸。'},
    {id:'authority-2',category:'practice',label:'实战干货',date:'08/28',title:'心理按钮·展示权威②：如何用开头让客户对你刮目相看？',body:['开头不要急着介绍产品。先说出客户正在经历的那个细节，让对方产生“你真的理解我”的感觉。','比如把“很多人不会成交”改成“每天都在发内容，却没人愿意继续聊”。具体场景比抽象结论更容易让读者停下来。'],quote:'先说清楚客户的处境，再说你的方法。'},
    {id:'authority-1',category:'practice',label:'实战干货',date:'08/28',title:'心理按钮·展示权威①：为什么客户相信你，却不一定向你付钱？',body:['信任只解决“愿不愿意听”，付费还需要看见清晰的路径。把服务对象、交付步骤和适用边界讲明白，客户才知道下一步怎么判断。','朋友圈里可以先回答一个高频疑问，再给出适合与不适合的人群，不用把所有卖点一次说完。'],quote:'信任是起点，清晰的选择路径才会带来行动。'},
    {id:'trust-1',category:'practice',label:'实战干货',date:'08/28',title:'心理按钮·建立信任：如何让陌生人第一次见你就愿意相信你？',body:['建立信任从真实开始：分享一次具体经历、一个当时的选择，以及你后来得到的认识。不要只展示结果，也让别人看到你如何做决定。','生活圈与专业圈穿插发布，先让客户了解你，再让客户知道你能解决什么问题。'],quote:'真实经历比漂亮口号更容易让人靠近。'},
    {id:'mutual-1',category:'practice',label:'实战干货',date:'08/26',title:'心理按钮·引发互惠①：为什么先付出的人更容易得到帮助？',body:['先付出不是无条件讨好，而是把一次有用的帮助交到对方手里。回答一个具体问题、给出一条可执行建议，都是轻量而真实的价值交换。','记录对方的问题和你的建议，后续再根据实际反馈跟进，别把没有发生的结果写成案例。'],quote:'先把问题讲清楚，成交自然会有更好的基础。'},
    {id:'mind-1',category:'mind',label:'心力成长',date:'08/25',title:'状态管理：为什么越想马上成交，越容易把客户推远？',body:['把结果看得太急，表达就会变成催促。先把今天能完成的一步做好：听清问题、给出判断、约定下一次沟通。','稳定的节奏会让客户有空间思考，也让你不必用夸张承诺换取短期回应。'],quote:'先稳住自己的节奏，客户才有安全感。'},
    {id:'community-1',category:'community',label:'社群解惑',date:'08/24',title:'客户说“我再想想”，下一句应该怎么接？',body:['先确认对方是在比较价格、评估适配，还是还没有理解方案。可以问：“你主要想再确认哪一部分？”把模糊的犹豫变成一个可以回答的问题。','如果对方暂时不需要，尊重她的决定并留下后续联系点，不要连续追问或制造焦虑。'],quote:'把追问换成澄清，沟通就会继续。'}
  ];
  const schoolCategoryName = (key) => ({all:'全部内容',practice:'实战干货',mind:'心力成长',community:'社群解惑'}[key] || '全部内容');
  const renderSchoolList = (category='all', query='') => { const list=$('schoolArticleList'); if(!list) return; const q=String(query||'').trim().toLowerCase(); const rows=SCHOOL_ARTICLES.filter(x=>(category==='all'||x.category===category)&&(!q||x.title.toLowerCase().includes(q)||x.body.join('').toLowerCase().includes(q))); list.innerHTML=rows.map(x=>`<button type="button" data-school-article="${esc(x.id)}"><span class="school-date">${esc(x.date)}</span><span class="school-article-meta"><small>${esc(x.label)}</small><strong>${esc(x.title)}</strong></span><span class="school-arrow">›</span></button>`).join('') || '<div class="list-card">暂时没有匹配内容</div>'; document.querySelectorAll('[data-school-category]').forEach(btn=>btn.classList.toggle('active', btn.dataset.schoolCategory===category)); const title=$('schoolListTitle'); if(title) title.textContent=schoolCategoryName(category); };
  const openSchoolList = (category='all', query='') => { renderSchoolList(category,query); showPanel('school-list',{push:true}); };
  const openSchoolArticle = (id) => { const item=SCHOOL_ARTICLES.find(x=>x.id===id); if(!item) return; $('schoolDetailCategory').textContent=item.label; $('schoolDetailTitle').textContent=item.title; $('schoolDetailDate').textContent=item.date; $('schoolDetailBody').innerHTML=`<h3>${esc(item.title)}</h3>${item.body.map(p=>`<p>${esc(p)}</p>`).join('')}<blockquote>${esc(item.quote)}</blockquote><div class="article-note">内容来自朋友圈印钞机方法论示例。涉及客户、成交和反馈的部分，请替换成你的真实素材后再发布。</div>`; showPanel('school-detail',{push:true}); };
  const renderSchoolFeatured = () => { const box=$('schoolFeatured'); if(!box) return; box.innerHTML=SCHOOL_ARTICLES.slice(0,1).map(x=>`<button type="button" data-school-article="${esc(x.id)}"><span class="school-date">今日</span><span class="school-article-meta"><small>${esc(x.label)}</small><strong>${esc(x.title)}</strong></span><span class="school-arrow">›</span></button>`).join(''); };
  async function generateIdea() { const input=$('ideaInput'); const text=input?.value.trim(); if (!text) throw new Error('请先写一件真实发生的事，或选择一个灵感切口'); const circle=input?.dataset.ideaCircle || ideaCircle(text); const payload = {happened:text, purpose:circle === '咨询圈' ? '消除疑虑' : circle === '反馈圈' ? '展示变化' : circle === '收款圈' ? '展示变化' : circle === '促成交圈' ? '推动行动' : '建立信任', circle_type:circle, image_urls:[]}; fill(payload); $('circle').value=circle; updateCircleUI(circle); showPanel('write'); await requestGeneration(payload); }
  let historyItems = [], uploadedImageUrl = '';
  async function loadList(type) { const endpoint = type === 'history' ? '/api/moments-coach/history' : type === 'materials' ? '/api/moments-coach/materials' : '/api/moments-coach/plans'; const data = await api(endpoint); const box = $(type === 'plan' ? 'plans' : type); if (!box) return; if (type === 'history') { historyItems = data.items || []; if ($('profileTotal')) $('profileTotal').textContent = historyItems.length; box.innerHTML = historyItems.map(x => `<article class="list-card"><header><span>${esc(x.circle_type || '朋友圈')}</span><span>${esc(String(x.created_at || '').replace('T',' ').slice(0,16))}</span></header><strong>${esc(x.title || '未命名文案')}</strong><pre>${esc(x.body || '')}</pre><button type="button" data-copy="${esc(x.body || '')}">复制文案</button></article>`).join('') || '<div class="list-card">还没有生成过草稿。</div>'; } else if (type === 'materials') { box.innerHTML = (data.items || []).map(x => `<article class="list-card" data-item='${esc(JSON.stringify(x))}'><header><span>真实素材</span><span>${esc(String(x.created_at || '').replace('T',' ').slice(0,16))}</span></header><strong>${esc(x.title || x.happened || '未命名素材')}</strong><pre>${esc(x.happened || x.current_change || '')}</pre><button type="button" data-use>用于写一条</button></article>`).join('') || '<div class="list-card">还没有保存素材。</div>'; } else { box.innerHTML = (data.items || []).map(x => `<article class="list-card"><header><span>已保存排期</span><span>${x.items?.length || 0} 条内容</span></header><strong>${esc(x.name || '朋友圈一周排期')}</strong></article>`).join('') || '<div class="list-card">还没有保存排期。</div>'; } }
  $('backBtn')?.addEventListener('click', goBackPanel);
  $('guideBtn')?.addEventListener('click', () => $('guide')?.classList.remove('hidden')); $('guideClose')?.addEventListener('click', () => $('guide')?.classList.add('hidden'));
  document.addEventListener('change', (e) => { if (!isCurrentCoach()) return; if (e.target.id === 'circle') updateCircleUI(e.target.value); });
  document.addEventListener('click', (e) => {
    if (!isCurrentCoach()) return;
    const schoolBack=e.target.closest('[data-school-back]');
    if (schoolBack) { e.preventDefault(); showPanel(schoolBack.dataset.schoolBack, {push:false, reset:false}); return; }
    const schoolCategory=e.target.closest('[data-school-category]');
    if (schoolCategory) { e.preventDefault(); const key=schoolCategory.dataset.schoolCategory || 'all'; if(currentPanel()==='school') openSchoolList(key); else renderSchoolList(key); return; }
    const schoolArticle=e.target.closest('[data-school-article]');
    if (schoolArticle) { e.preventDefault(); openSchoolArticle(schoolArticle.dataset.schoolArticle); return; }
    const schoolSearch=e.target.closest('[data-school-search]');
    if (schoolSearch) { e.preventDefault(); openSchoolList('all', $('schoolSearch')?.value || ''); return; }
  });
  $('ideaInput')?.addEventListener('input', (e) => { if (!e.isTrusted) return; delete e.target.dataset.ideaCircle; });
  document.addEventListener('click', async (e) => { if (!isCurrentCoach()) return; const tab=e.target.closest('[data-tab]'); if(tab){e.preventDefault();showPanel(tab.dataset.tab);return;} const back=e.target.closest('[data-back-panel]'); if(back){e.preventDefault();goBackPanel();return;} const go=e.target.closest('[data-go]'); if(go){e.preventDefault(); if(go.dataset.go === 'home') { showPanel('home', {push:false}); } else { showPanel(go.dataset.go, {push:false}); } return;} const home=e.target.closest('[data-home-action]'); if(home){const a=home.dataset.homeAction; if(a==='image')showPanel('image',{push:true}); else if(a==='inspire')showPanel('choose',{push:true}); else if(a==='choose')showPanel('idea',{push:true}); else if(a==='school')showPanel('school',{push:true}); return;} const idea=e.target.closest('[data-idea-key]'); if(idea){const sample=IDEA_PROMPTS[idea.dataset.ideaKey]; if(sample){$('ideaInput').value=sample.text; $('ideaInput').dataset.ideaCircle=sample.circle; $('ideaInput').focus();} return;} if(e.target.closest('[data-generate-idea]')){try{await generateIdea();}catch(x){toast(x.message);}return;} const circle=e.target.closest('[data-circle]'); if(circle){$('circle').value=circle.dataset.circle;updateCircleUI(circle.dataset.circle);showPanel('write',{push:true});return;} const use=e.target.closest('[data-use]'); if(use){try{fill(JSON.parse(use.closest('[data-item]').dataset.item));showPanel('write',{push:true});}catch{x=>toast('素材读取失败');}return;} const copy=e.target.closest('[data-copy]'); if(copy){navigator.clipboard?.writeText(copy.dataset.copy||'').then(()=>toast('文案已复制')).catch(()=>toast('复制失败'));return;} const imageText=e.target.closest('[data-generate-image-text]'); if(imageText){if(!uploadedImageUrl){toast('请先选择图片');return;} try{const data=await api('/api/moments-coach/generate',{method:'POST',json:{happened:$('imagePrompt').value.trim()||'请根据图片生成适合朋友圈的文案',image_urls:[uploadedImageUrl],purpose:'建立信任'},timeoutMs:300000});fill({happened:$('imagePrompt').value.trim(),image_urls:[uploadedImageUrl],purpose:'建立信任'});showPanel('write',{push:true});renderResults(data.items);}catch(x){toast(x.message);}return;} const result=e.target.closest('.result'); if(result && e.target.closest('[data-image]')){const btn=e.target.closest('[data-image]');btn.disabled=true;try{const d=await api(`/api/moments-coach/${result.dataset.id}/generate-image`,{method:'POST',json:{},timeoutMs:300000});btn.textContent='配图已生成';const img=document.createElement('img');img.src=d.image_url;img.alt='生成配图';img.style='width:100%;margin:10px 0;border-radius:14px';result.insertBefore(img,result.querySelector('.confirm'));}catch(x){btn.disabled=false;toast(x.message);}return;} if(result && e.target.closest('[data-publish]')){if(!result.querySelector('[data-confirm]')?.checked){toast('请先确认素材真实且已人工核对');return;}toast('发布任务已提交');} });
  $('saveMaterial')?.addEventListener('click', async () => {try{await api('/api/moments-coach/materials',{method:'POST',json:snapshot()});toast('素材已保存');}catch(x){toast(x.message);}}); $('saveMaterialTop')?.addEventListener('click', async () => {try{await api('/api/moments-coach/materials',{method:'POST',json:snapshot()});toast('素材已保存');}catch(x){toast(x.message);}}); $('generate')?.addEventListener('click', async () => {try{await generate();}catch(x){toast(x.message);}});
  $('imageFile')?.addEventListener('change', async (e) => { const file=e.target.files?.[0]; if(!file)return; const status=$('imageStatus'), preview=$('imagePreview'); if(preview){preview.src=URL.createObjectURL(file);preview.classList.remove('hidden');} e.target.disabled=true; if(status)status.textContent='正在上传图片…'; try{const fd=new FormData();fd.append('file',file,file.name||'image.jpg');const d=await api('/api/h5-chat/uploads',{method:'POST',body:fd,timeoutMs:60000});uploadedImageUrl=d.url||'';if(!uploadedImageUrl)throw new Error('服务器未返回图片地址');if(status)status.textContent='图片已上传，可以点击生成文案';}catch(x){if(status)status.textContent='上传失败：'+x.message;toast(x.message);}finally{e.target.disabled=false;} });
  // Handle publishing in capture phase so the request is actually queued before the legacy click handler.
  document.addEventListener('click', async (e) => {
    if (!isCurrentCoach()) return;
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
    if (!isCurrentCoach()) return;
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
  try{fill(JSON.parse(localStorage.getItem('lobster_moments_coach_draft')||'null')||{});}catch{} renderSchoolFeatured(); updateCircleUI($('circle')?.value || ''); showPanel('home');
  window.__momentsCoachBack = () => {
    if (!isCurrentCoach()) return false;
    if (currentPanel() !== 'home' || panelHistory.length) { goBackPanel(); return true; }
    return false;
  };
})();
