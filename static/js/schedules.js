// static/js/schedules.js — aba Agendamentos (Fase 6)

async function loadSchedules(){
  try{
    const r = await fetch('/api/schedules/status');
    const j = await r.json();
    scheduleById = {};
    (j.schedules||[]).forEach(s=>{ scheduleById[s.product_id]=s; });
    scheduleQueue = j.queue||[];
    scheduleMeta = {scheduler_running: !!j.scheduler_running, has_scheduler: !!j.has_scheduler, queue_length: j.queue_length||0};
    return j;
  }catch(e){ return null; }
}

function renderScheduleStatus(){
  const j = scheduleById;
  let active = Object.values(j).filter(s=>s.enabled).length;
  let el = $('#scheduleStatus');
  if(!el){
    const container = $('#productsStatus');
    el = document.createElement('div'); el.id='scheduleStatus'; el.className='small muted'; el.style.marginTop='6px';
    container.parentNode.insertBefore(el, container.nextSibling);
  }
  // queue display normalizada
  let qTxt='';
  if(scheduleQueue.length){
    const qStr = scheduleQueue.map(item=>{
      if(typeof item==='string') return item;
      if(item.product_ids) return item.product_ids.join(',');
      if(item.product_id) return item.product_id;
      return JSON.stringify(item);
    }).join(', ');
    qTxt = ` | fila: ${qStr} (${scheduleQueue.length})`;
  }
  if(active===0){
    el.innerHTML = `⏰ agendamento: nenhum produto com busca automática. <span class="mono">Ative no modal de edição.</span>${qTxt} <span class="mono">${scheduleMeta.scheduler_running?'scheduler ok':'scheduler off'}</span>`;
  } else {
    const details = Object.values(j).filter(s=>s.enabled).map(s=>{
      const modeTxt = s.mode==='cron' ? `cron ${escapeHtml(s.cron||'')}` : formatInterval(s.interval_minutes);
      const jitterTxt = s.jitter_minutes ? ` +${s.jitter_minutes}j` : '';
      return `${escapeHtml(s.product_id)}: ${modeTxt}${jitterTxt} próx ${formatNextRun(s.next_run)}`;
    }).join(' · ');
    el.innerHTML = `⏰ agendamento: ${active} produto(s) automático(s) — ${details}${qTxt}`;
  }
  const badge=$('#schedCountBadge');
  if(badge) badge.textContent = active ? `(${active})` : '';
  const scWrap=$('#schedStatusHeader');
  if(scWrap){
    scWrap.innerHTML = `Scheduler: ${scheduleMeta.has_scheduler ? (scheduleMeta.scheduler_running ? '<span class="badge ok">● rodando</span>' : '<span class="badge warn">pausado</span>') : '<span class="badge err">sem APScheduler</span>'} | ${active} ativo(s) | fila ${scheduleQueue.length} ${qTxt ? '('+qTxt.slice(7)+')' : ''}`;
  }
}

function renderSchedulesTable(){
  const bod=$('#schedBody');
  if(!bod) return;
  if(!products.length){ bod.innerHTML=`<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">nenhum produto</td></tr>`; return; }
  bod.innerHTML='';
  for(const p of products){
    const s = scheduleById[p.id] || {};
    const prodSched = p.schedule || {};
    const enabled = !!prodSched.enabled;
    const mode = prodSched.mode || s.mode || 'interval';
    let agendamentoHtml='';
    if(!enabled){
      agendamentoHtml=`<span class="pill" style="opacity:.6">manual</span> ${p.enabled?'':'<span class="pill off">produto off</span>'}`;
    } else if(mode==='cron'){
      agendamentoHtml=`<span class="pill match">cron</span> <span class="mono">${escapeHtml(prodSched.cron||s.cron||'')}</span> ${prodSched.jitter_minutes?`<span class="small muted">+${prodSched.jitter_minutes}j</span>`:''}`;
    } else {
      agendamentoHtml=`<span class="pill match">${formatInterval(prodSched.interval_minutes)}</span> ${prodSched.jitter_minutes?`<span class="small muted">+${prodSched.jitter_minutes}j</span>`:''}`;
    }
    const nextRun = enabled ? formatNextRun(s.next_run) : '—';
    const lastRun = s.last_run ? formatLastRun(s.last_run) : '<span class="muted">nunca</span>';
    const status = s.last_status ? (s.last_status==='ok' ? `<span class="pill match">ok ${s.last_matched!=null?`·${s.last_matched}`:''}</span>` : `<span class="pill off">erro</span> <span class="small muted mono">${escapeHtml((s.last_error||'').slice(0,40))}</span>`) : '<span class="muted">—</span>';
    const tr=document.createElement('tr');
    tr.innerHTML=`
      <td><div style="font-weight:600">${escapeHtml(p.name)}</div><div class="small mono muted">${escapeHtml(p.id)} ${p.enabled?'':'[off]'}</div></td>
      <td>${agendamentoHtml}<div class="small muted mono">${escapeHtml(mode)} ${mode==='interval'? `· ${prodSched.interval_minutes||s.interval_minutes||'—'}min` : ''}</div></td>
      <td class="small mono">${nextRun}</td>
      <td class="small">${lastRun}</td>
      <td>${status}</td>
      <td><div class="row">
        <button class="btn small" onclick="triggerNow('${escapeAttr(p.id)}')">▶️ Agora</button>
        <button class="btn small" onclick="triggerNow('${escapeAttr(p.id)}', true)">⏳ Fila</button>
        <button class="btn small" onclick="openProductModal('${escapeAttr(p.id)}')">Editar</button>
      </div></td>
    `;
    bod.appendChild(tr);
  }
  const qInfo=$('#schedQueueInfo');
  const scrapeQInfo=$('#scrapeQueueInfo');
  const qTxt = scheduleQueue.length ? scheduleQueue.map(i=>{
    if(typeof i==='string') return i;
    if(i.product_ids) return i.product_ids.join(',');
    return JSON.stringify(i);
  }).join(', ') : 'vazia';
  const html=`Fila: ${scheduleQueue.length} — ${escapeHtml(qTxt)} ${scheduleQueue.length? `<button class="btn small" onclick="clearQueue()" style="margin-left:6px">limpar</button>`:''} <span class="muted">| scheduler ${scheduleMeta.scheduler_running?'ok':'off'}</span>`;
  if(qInfo) qInfo.innerHTML=html;
  if(scrapeQInfo) scrapeQInfo.innerHTML=html;
}

async function loadSchedulesAndRender(){
  await loadSchedules();
  renderScheduleStatus();
  renderProducts();
  renderSchedulesTable();
}

async function triggerNow(pid, useQueue=false){
  const url = `/api/schedules/trigger/${encodeURIComponent(pid)}${useQueue?'?queue=1':''}`;
  const r=await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({})});
  const j=await r.json().catch(()=>({}));
  if(r.status===202){
    alert(`Enfileirado na posição ${j.position}`);
    loadSchedulesAndRender();
    switchTab('scrape'); startPolling();
  } else if(!r.ok){
    if(r.status===409 && confirm('Ocupado. Enfileirar?')){
      return triggerNow(pid, true);
    }
    alert('Erro: '+(j.error||'falha'));
  } else {
    switchTab('scrape'); startPolling();
  }
}

async function clearQueue(){
  if(!confirm('Limpar fila de agendamentos?')) return;
  const r=await fetch('/api/schedules/queue', {method:'DELETE'});
  const j=await r.json();
  alert(`Fila limpa: ${j.cleared||0} removidos`);
  await loadSchedulesAndRender();
}

// global telegram
