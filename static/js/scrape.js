// static/js/scrape.js — aba Buscar (forcer scrape + polling) (Fase 6)

async function loadProductsForScrape(){
  const c = $('#scrapeProducts');
  c.innerHTML='';
  if(!products.length){ c.innerHTML='<span class="muted">Nenhum produto</span>'; return; }
  for(const p of products){
    const label=document.createElement('label');
    label.style.cssText='display:flex;gap:8px;align-items:center;text-transform:none;letter-spacing:0;font-weight:500;color:var(--fg);margin:4px 0';
    const dis = p.enabled ? '' : ' disabled';
    label.innerHTML=`<input type="checkbox" value="${escapeAttr(p.id)}" ${p.enabled?'checked':''}${dis} style="width:auto"> <span>${escapeHtml(p.name)} <span class="mono muted">(${escapeHtml(p.id)})</span> — ${escapeHtml(p.queries.slice(0,2).join(', '))}${p.queries.length>2?'...':''} ${p.enabled?'':'[desativado]'}</span>`;
    c.appendChild(label);
  }
}

function toggleAllScrape(v){
  $('#scrapeProducts').querySelectorAll('input[type=checkbox]:not(:disabled)').forEach(cb=>cb.checked=v);
}

async function startScrape(){
  const checked = [...$('#scrapeProducts').querySelectorAll('input:checked')].map(cb=>cb.value);
  const body={};
  if(checked.length && checked.length !== products.filter(p=>p.enabled).length){
    body.product_ids = checked;
  } else if(checked.length===0){
    alert('Selecione ao menos um produto');
    return;
  }
  if($('#ovPlaywright').checked) body.playwright_only=true;
  if($('#ovQueue').checked) body.queue=true;
  $('#btnScrape').disabled=true; $('#scrapeState').textContent='iniciando...'; $('#liveLog').textContent=''; lastLogLen=0;
  let url='/api/scrape';
  if(body.queue) url+='?queue=1';
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(r.status===202){
    $('#scrapeState').textContent=`enfileirado pos ${j.position}`; $('#btnScrape').disabled=false;
    loadSchedulesAndRender();
    return;
  }
  if(!r.ok){
    if(r.status===409 && confirm('Ocupado. Enfileirar na fila?')){
      body.queue=true;
      const r2=await fetch('/api/scrape?queue=1',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const j2=await r2.json();
      if(r2.ok || r2.status===202){ $('#scrapeState').textContent=`enfileirado pos ${j2.position}`; loadSchedulesAndRender(); }
      else $('#scrapeState').textContent='erro: '+(j2.error||'falha');
    } else {
      $('#scrapeState').textContent='erro: '+(j.error||'falha');
    }
    $('#btnScrape').disabled=false; return;
  }
  $('#scrapeState').textContent='rodando...'; $('#progressBox').style.display='block';
  startPolling();
}

function clearLog(){ $('#liveLog').textContent=''; }

function copyLog(){ navigator.clipboard.writeText($('#liveLog').textContent); }
let polling=false;

function startPolling(){
  if(polling) return; polling=true;
  if(pollTimer) clearInterval(pollTimer);
  pollTimer=setInterval(pollScrape, 1000);
  pollScrape();
}

async function pollScrape(){
  try{
    const r=await fetch('/api/scrape/status'); const j=await r.json();
    const running=j.running;
    $('#scrapeState').textContent = running ? 'rodando...' : (j.error ? 'erro' : 'ocioso');
    $('#scrapeState').className = 'badge '+(running?'warn': j.error?'err':'');
    const p=j.progress||{};
    $('#pProduct').textContent = p.current_product_name || p.current_product || '—';
    $('#pQuery').textContent = p.current_query||'—';
    $('#pPage').textContent = p.current_page||'—';
    const tp=p.total_products||0;
    if(tp) $('#pStats').textContent = `${p.current_product||''} ${p.current_query||''} p${p.current_page||''} — ${tp} produto(s)`;
    $('#pStats').textContent += ` | matches: ${j.matched_count||0} | total: ${j.results_count||0}` + (j.started_at?` | início: ${new Date(j.started_at).toLocaleTimeString()}`:'');
    if(p.total_products && p.total_pages){
      $('#pBar').style.width = running ? '45%' : '100%';
    } else {
      $('#pBar').style.width = running ? '30%' : '100%';
    }
    const logs=j.logs||[];
    if(logs.length !== lastLogLen){
      $('#liveLog').textContent = logs.join('\n');
      $('#liveLog').scrollTop = $('#liveLog').scrollHeight;
      lastLogLen=logs.length;
    }
    if(!running){
      polling=false; clearInterval(pollTimer); pollTimer=null;
      $('#btnScrape').disabled=false;
      $('#lastResult').textContent = j.error ? `erro: ${j.error}` : `${j.matched_count} matches, ${j.results_count} resultados (${new Date(j.finished_at).toLocaleString()})`;
      if(!j.error) loadDeals();
      loadSchedulesAndRender();
    }
  }catch(e){ }
}

// results
let debounceTimer=null;
