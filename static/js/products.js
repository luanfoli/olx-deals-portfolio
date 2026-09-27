// static/js/products.js — aba Produtos (CRUD + modal + config global) (Fase 6)

function toggleScheduleFields(){
  const on = $('#mScheduleEnabled').checked;
  $('#scheduleFields').style.display = on ? '' : 'none';
  if(!on) return;
  const mode = $('#mScheduleMode').value;
  const isCron = mode==='cron';
  $('#scheduleIntervalGroup').style.display = isCron ? 'none' : '';
  $('#scheduleCronGroup').style.display = isCron ? '' : 'none';
  // hint
  let hint='';
  const jitter = parseInt($('#mScheduleJitter').value||'0');
  const jitterTxt = jitter ? ` + jitter ±${jitter}min` : '';
  if(isCron){
    const cron = $('#mScheduleCron').value||'0 */6 * * *';
    hint = `cron "${cron}"${jitterTxt} — fuso America/Sao_Paulo`;
    validateCron();
  } else {
    const mins = parseInt($('#mScheduleInterval').value||'360');
    hint = `próx ≈ ${mins}min após salvar${jitterTxt}`;
  }
  $('#scheduleHint').textContent = hint;
}

function validateCron(){
  const cron = ($('#mScheduleCron').value||'').trim();
  const el=$('#cronValidation');
  if(!cron){ el.textContent=''; return; }
  const parts=cron.split(/\s+/);
  if(parts.length!==5){
    el.textContent='✗ deve ter 5 campos (ex: 0 */6 * * *)'; el.style.color='var(--err)';
    return;
  }
  // validação básica; backend valida completo
  el.textContent='✓ formato 5 campos — validação completa no salvar'; el.style.color='var(--muted)';
}
$('#mScheduleCron')?.addEventListener('input', ()=>{ toggleScheduleFields(); });
$('#mScheduleMode')?.addEventListener('change', toggleScheduleFields);
$('#mScheduleInterval')?.addEventListener('change', toggleScheduleFields);
$('#mScheduleJitter')?.addEventListener('input', toggleScheduleFields);


async function loadProducts(){
  $('#productsStatus').textContent='Carregando...';
  try{
    const [rProd, rCfg, rSched] = await Promise.all([fetch('/api/products'), fetch('/api/config'), loadSchedules()]);
    const jProd = await rProd.json();
    cfg = await rCfg.json();
    products = jProd.products || cfg.products || [];
    $('#tgToken').value = cfg.telegram_token||'';
    $('#tgChatId').value = cfg.telegram_chat_id||'';
    renderProducts();
    populateProductFilter();
    await loadProductsForScrape();
    renderScheduleStatus();
    renderSchedulesTable();
    $('#productsStatus').textContent = `${products.length} produto(s) — clique em Editar para ajustar preço/limite.`;
  }catch(e){
    $('#productsStatus').textContent='Erro ao carregar: '+e;
  }
}

function renderProducts(){
  const g = $('#productsGrid');
  g.innerHTML='';
  if(!products.length){ $('#noProducts').style.display='block'; return; }
  $('#noProducts').style.display='none';
  for(const p of products){
    const card = document.createElement('div');
    card.className='product-card'+(p.enabled?'':' disabled');
    const price = (p.min_price!=null || p.max_price!=null) ? `R$ ${p.min_price!=null?p.min_price:'-'} → R$ ${p.max_price!=null?p.max_price:'∞'}` : 'sem limite de preço';
    const queries = (p.queries||[]).slice(0,4).map(q=>`<span class="tag">${escapeHtml(q)}</span>`).join('') + ((p.queries||[]).length>4?` <span class="small muted">+${p.queries.length-4}</span>`:'');
    const excludes = (p.exclude_keywords||[]);
    const exclHtml = excludes.length ? `<div class="small mono" style="margin-top:2px">excluir: ${escapeHtml(excludes.slice(0,5).join(', '))}${excludes.length>5?` +${excludes.length-5} mais`:''}</div>` : '';
    const sched = (p.schedule||{});
    const schedInfo = scheduleById[p.id] || {};
    let schedBadge = '';
    let lastRunHtml='';
    if(sched.enabled){
      if(sched.mode==='cron'){
        schedBadge = `<span class="pill match">⏰ cron ${escapeHtml(sched.cron||'')}</span>`;
      } else {
        schedBadge = `<span class="pill match">⏰ ${formatInterval(sched.interval_minutes)}</span>`;
      }
      if(sched.jitter_minutes) schedBadge += ` <span class="small muted">+${sched.jitter_minutes} jitter</span>`;
      if(schedInfo.next_run){
        schedBadge += ` <span class="small muted mono">próx ${formatNextRun(schedInfo.next_run)}</span>`;
      }
      if(schedInfo.last_run){
        lastRunHtml = `<div class="small muted">última: ${formatLastRun(schedInfo.last_run)} ${schedInfo.last_status==='ok'?'<span class="pill match">ok</span>': schedInfo.last_status==='error'? `<span class="pill off">erro: ${escapeHtml((schedInfo.last_error||'').slice(0,60))}</span>` : ''} ${schedInfo.last_matched!=null?`· ${schedInfo.last_matched} matches`:''}</div>`;
      } else {
        lastRunHtml = `<div class="small muted">última: nunca</div>`;
      }
    } else {
      schedBadge = `<span class="pill" style="opacity:.6">⏰ manual</span>`;
      if(schedInfo.last_run){
        lastRunHtml = `<div class="small muted">última: ${formatLastRun(schedInfo.last_run)} ${schedInfo.last_matched!=null?`· ${schedInfo.last_matched} matches`:''}</div>`;
      }
    }
    card.innerHTML = `
      <div class="product-head">
        <div>
          <div class="product-title">${escapeHtml(p.name)} ${p.draft?'<span class="pill warn">rascunho</span>':(p.enabled?'<span class="pill match">ativo</span>':'<span class="pill off">desativado</span>')} ${schedBadge}</div>
          <div class="product-id mono">${escapeHtml(p.id)} · ${p.pages} pág/query · ${escapeHtml(price)}</div>
        </div>
        <div class="row">
          <button class="btn small" onclick="openProductModal('${escapeAttr(p.id)}')">Editar</button>
          <button class="btn small" onclick="toggleEnabled('${escapeAttr(p.id)}')">${p.enabled?'Desativar':'Ativar'}</button>
          <button class="btn small" title="Disparar agora (se ocupado enfileira)" onclick="triggerNow('${escapeAttr(p.id)}')">▶️ Agora</button>
          <button class="btn small" style="color:var(--err)" onclick="deleteProduct('${escapeAttr(p.id)}')">Excluir</button>
        </div>
      </div>
      <div class="small">${queries||'<span class="muted">sem queries</span>'}</div>
      <div class="small mono muted" style="word-break:break-all">${escapeHtml(p.search_url||'')}</div>
      ${p.junk_pattern?`<div class="small mono">junk: ${escapeHtml(p.junk_pattern.slice(0,80))}${p.junk_pattern.length>80?'...':''}</div>`:''}
      ${exclHtml}
      ${lastRunHtml}
    `;
    g.appendChild(card);
  }
}

function populateProductFilter(){
  const sel = $('#filterProduct');
  const cur = sel.value;
  sel.innerHTML='<option value="all">Todos os produtos</option>';
  for(const p of products){
    const o=document.createElement('option');
    o.value=p.id; o.textContent=`${p.name} (${p.id})`;
    sel.appendChild(o);
  }
  if([...sel.options].some(o=>o.value===cur)) sel.value=cur;
}

// modal
let mQueries = [];
let mExcludes = [];

function openProductModal(id){
  editingId = id;
  const isNew = !id;
  $('#modalTitle').textContent = isNew ? 'Novo produto' : 'Editar produto';
  $('#modalStatus').textContent = '';
  if(isNew){
    $('#mName').value=''; $('#mId').value=''; mQueries=[]; mExcludes=[]; $('#mMinPrice').value=''; $('#mMaxPrice').value=''; $('#mPages').value='2'; $('#mEnabled').checked=true; $('#mPlaywright').checked=false;
    $('#mSearchUrl').value='https://www.olx.com.br/brasil?q={query}&opst=2&page={page}';
    $('#mJunk').value='\\b(dock|station|suporte|at[eé]|docking|encosto|cabo|clip|gabinete|case|c[aá]mera)\\b|recupera|reparo|conserto|quebrado|defeito|p/?a/?r/?a?\\s?pecas|pecas';
    $('#mBulk').value='';
    $('#mScheduleEnabled').checked=false;
    $('#mScheduleMode').value='interval';
    $('#mScheduleInterval').value='360';
    $('#mScheduleCron').value='0 */6 * * *';
    $('#mScheduleJitter').value='0';
  } else {
    const p = products.find(x=>x.id===id);
    if(!p) return;
    $('#mName').value=p.name; $('#mId').value=p.id; mQueries=[...(p.queries||[])]; mExcludes=[...(p.exclude_keywords||[])];
    $('#mMinPrice').value = p.min_price!=null?p.min_price:'';
    $('#mMaxPrice').value = p.max_price!=null?p.max_price:'';
    $('#mPages').value = String(p.pages);
    $('#mEnabled').checked = !!p.enabled;
    $('#mPlaywright').checked = !!p.playwright_only;
    $('#mSearchUrl').value = p.search_url;
    $('#mJunk').value = p.junk_pattern||'';
    $('#mBulk').value='';
    const s = p.schedule || {enabled:false, mode:'interval', interval_minutes:360, jitter_minutes:0, cron:'0 */6 * * *'};
    $('#mScheduleEnabled').checked = !!s.enabled;
    $('#mScheduleMode').value = s.mode||'interval';
    $('#mScheduleInterval').value = String(s.interval_minutes || 360);
    $('#mScheduleCron').value = s.cron || '0 */6 * * *';
    $('#mScheduleJitter').value = String(s.jitter_minutes||0);
  }
  renderMChips();
  toggleScheduleFields();
  $('#productModal').classList.add('open');
}

function closeProductModal(){ $('#productModal').classList.remove('open'); editingId=null; }

function renderMChips(){
  const c = $('#mChips'); c.innerHTML='';
  mQueries.forEach((q,i)=>{
    const d=document.createElement('span'); d.className='query-chip'; d.innerHTML=`${escapeHtml(q)} <button onclick="removeMQuery(${i})">×</button>`;
    c.appendChild(d);
  });
  if(!mQueries.length) c.innerHTML='<span class="muted small">nenhuma query — adicione ao menos uma</span>';
  const ec = $('#mExcludeChips');
  if(ec){
    ec.innerHTML='';
    mExcludes.forEach((t,i)=>{
      const d=document.createElement('span'); d.className='query-chip'; d.innerHTML=`${escapeHtml(t)} <button onclick="removeMExclude(${i})">×</button>`;
      ec.appendChild(d);
    });
    if(!mExcludes.length) ec.innerHTML='<span class="muted small">nenhum termo para excluir</span>';
  }
}

function addMQuery(){
  const v=$('#mNewQuery').value.trim(); if(!v) return;
  if(mQueries.length>=50){ $('#modalStatus').textContent='Máximo 50 queries'; return; }
  mQueries.push(v); $('#mNewQuery').value=''; renderMChips();
}

function removeMQuery(i){ mQueries.splice(i,1); renderMChips(); }

function addMExclude(){
  const v=$('#mNewExclude').value.trim(); if(!v) return;
  if(mExcludes.length>=50){ $('#modalStatus').textContent='Máximo 50 termos de exclusão'; return; }
  if(mExcludes.some(t=>t.toLowerCase()===v.toLowerCase())){ $('#mNewExclude').value=''; return; }
  if(v.length>40){ $('#modalStatus').textContent='Termo máximo 40 chars'; return; }
  mExcludes.push(v); $('#mNewExclude').value=''; $('#modalStatus').textContent=''; renderMChips();
}

function removeMExclude(i){ mExcludes.splice(i,1); renderMChips(); }

function applyMBulk(){
  const raw=$('#mBulk').value; if(!raw.trim()) return;
  const parts=raw.split(/[,\n]+/).map(s=>s.trim()).filter(Boolean);
  const set = new Set([...mQueries, ...parts]);
  mQueries = [...set].slice(0,50);
  renderMChips(); $('#mBulk').value='';
}

async function saveProduct(){
  const mode = $('#mScheduleMode').value;
  const payload = {
    name: $('#mName').value.trim(),
    queries: mQueries,
    exclude_keywords: mExcludes,
    min_price: $('#mMinPrice').value==='' ? null : parseFloat($('#mMinPrice').value),
    max_price: $('#mMaxPrice').value==='' ? null : parseFloat($('#mMaxPrice').value),
    pages: parseInt($('#mPages').value),
    search_url: $('#mSearchUrl').value.trim(),
    junk_pattern: $('#mJunk').value,
    playwright_only: $('#mPlaywright').checked,
    enabled: $('#mEnabled').checked,
    schedule: {
      enabled: $('#mScheduleEnabled').checked,
      mode: mode,
      interval_minutes: parseInt($('#mScheduleInterval').value),
      cron: $('#mScheduleCron').value.trim() || '0 */6 * * *',
      jitter_minutes: parseInt($('#mScheduleJitter').value||'0'),
      timezone: 'America/Sao_Paulo'
    }
  };
  const idVal = $('#mId').value.trim();
  if(idVal) payload.id = idVal;
  if(!payload.name){ $('#modalStatus').textContent='Nome obrigatório'; return; }
  if(!payload.queries.length){ $('#modalStatus').textContent='Adicione ao menos uma query'; return; }
  if(payload.schedule.enabled && payload.schedule.mode==='cron' && payload.schedule.cron.split(/\s+/).length!==5){
    $('#modalStatus').textContent='Cron deve ter 5 campos'; return;
  }
  $('#modalStatus').textContent='Salvando...';
  try{
    let r;
    if(editingId){
      r = await fetch('/api/products/'+encodeURIComponent(editingId), {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    } else {
      r = await fetch('/api/products', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    }
    const j=await r.json();
    if(!r.ok){ $('#modalStatus').textContent='Erro: '+(j.errors||[j.error]).join('; '); return; }
    closeProductModal();
    await loadProducts();
  }catch(e){ $('#modalStatus').textContent='Erro: '+e; }
}

async function deleteProduct(id){
  if(!confirm(`Excluir produto "${id}"? Histórico permanece em deals.json.`)) return;
  const r=await fetch('/api/products/'+encodeURIComponent(id), {method:'DELETE'});
  const j=await r.json();
  if(!r.ok){ alert('Erro: '+(j.errors||[j.error]).join('; ')); return; }
  await loadProducts();
}

async function toggleEnabled(id){
  const p = products.find(x=>x.id===id);
  if(!p) return;
  const r=await fetch('/api/products/'+encodeURIComponent(id), {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled: !p.enabled})});
  const j=await r.json();
  if(!r.ok){ alert('Erro: '+(j.errors||[j.error]).join('; ')); return; }
  await loadProducts();
}

async function saveGlobalConfig(){
  const payload={
    telegram_token: $('#tgToken').value.trim()||null,
    telegram_chat_id: $('#tgChatId').value.trim()? (isNaN($('#tgChatId').value.trim()) ? $('#tgChatId').value.trim() : parseInt($('#tgChatId').value.trim())) : null,
  };
  $('#globalStatus').textContent='Salvando...';
  const r=await fetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const j=await r.json();
  if(!r.ok){ $('#globalStatus').textContent='Erro: '+(j.errors||[j.error]).join('; '); }
  else { $('#globalStatus').textContent='✓ Salvo!'; cfg=j.config; }
  setTimeout(()=>$('#globalStatus').textContent='',3000);
}

async function testTelegram(){
  $('#tgTestRes').textContent='enviando...';
  const tk=$('#tgToken').value.trim();
  // valor mascarado (***xxxx) = usar o armazenado no servidor (token:null)
  const body={token:(tk&&tk.indexOf('***')!==0)?tk:null, chat_id:$('#tgChatId').value.trim()};
  const r=await fetch('/api/config/telegram/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  $('#tgTestRes').textContent = j.ok ? '✓ enviado' : '✗ '+(j.error||'erro');
}

async function doLogin(){
  $('#globalStatus').textContent='Login iniciado... veja Buscar > log';
  switchTab('scrape');
  const r=await fetch('/api/login',{method:'POST'});
  if(r.ok) startPolling();
}

// scrape
