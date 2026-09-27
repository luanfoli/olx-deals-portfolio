// static/js/tuner.js — aba Tuner + LLM config (Fase 6)

function debouncedTunerLoad(){ clearTimeout(tunerDebounce); tunerDebounce=setTimeout(loadTunerRequests, 350); }

async function loadTunerRequests(){
  const params=new URLSearchParams({
    status: $('#tunerStatusFilter').value,
    q: $('#tunerQ').value.trim(),
    sort: $('#tunerSort').value,
    order: $('#tunerOrder').value,
    limit: 50,
  });
  for(const [k,v] of [...params]) if(!v || v==='all') params.delete(k);
  const r=await fetch('/api/tuner/requests?'+params);
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ $('#tunerStats').textContent='Erro: '+(j.error||j.errors?.join('; ')); return; }
  tunerRequests=j.requests||[];
  $('#tunerStats').textContent=`${j.total} pedido(s) — exibindo ${j.count}`;
  $('#tunerBadge').textContent=j.total?`(${j.total})`:'';
  renderTunerTable();
}

function renderTunerTable(){
  const tb=$('#tunerBody');
  if(!tunerRequests.length){ tb.innerHTML='<tr><td colspan="7" class="muted" style="text-align:center;padding:24px">nenhum pedido — clique em + Novo pedido</td></tr>'; return; }
  tb.innerHTML='';
  for(const req of tunerRequests){
    const tr=document.createElement('tr');
    const statusColor = req.status==='ativado' ? 'match' : req.status==='descartado' ? 'off' : req.status==='aguardando_aprovacao' ? 'warn' : '';
    const lastIter = (req.iterations||[]).slice(-1)[0];
    const prec = lastIter ? (lastIter.precision*100).toFixed(0)+'%' : '—';
    const itCount = (req.iterations||[]).length;
    const draft = req.draft_product_id||'—';
    const briefing = req.briefing||{};
    const desc = briefing.descricao||req.id;
    // LLM badge para última iteração
    let llmBadge = '<span class="small muted">—</span>';
    if(lastIter && lastIter.llm){
      const llm = lastIter.llm;
      if(llm.used){
        llmBadge = `<span class="pill match">✓ LLM ${escapeHtml(llm.model||llm.provider||'')}</span><div class="small mono muted">${llm.latency_ms??'—'}ms</div>`;
      } else {
        llmBadge = `<span class="pill off">heurística</span><div class="small muted">${escapeHtml((llm.error||'fallback').slice(0,30))}</div>`;
      }
    } else if(lastIter){
      llmBadge = '<span class="pill off">sem LLM</span>';
    }
    tr.innerHTML=`
      <td><div style="font-weight:600">${escapeHtml(desc.slice(0,60))}</div><div class="small mono muted">${escapeHtml(req.id)}</div><div class="small muted">${escapeHtml(briefing.definicao||'').slice(0,60)}</div></td>
      <td class="mono small">${escapeHtml(draft)}</td>
      <td><span class="pill ${statusColor}">${escapeHtml(req.status)}</span></td>
      <td class="small">${itCount}/4</td>
      <td class="small mono">${prec}</td>
      <td class="small">${llmBadge}</td>
      <td><div class="row">
        <button class="btn small" onclick="showTunerDetail('${escapeAttr(req.id)}')">Ver</button>
        ${req.status==='aguardando_aprovacao' ? `<button class="btn small primary" onclick="tunerAction('${escapeAttr(req.id)}','activate')">▶️ Ativar</button><button class="btn small" onclick="tunerAction('${escapeAttr(req.id)}','keep_draft')">Manter</button><button class="btn small" style="color:var(--err)" onclick="tunerAction('${escapeAttr(req.id)}','discard')">Descartar</button>` : ''}
        ${req.status==='em_refinamento' ? '<span class="small muted">refinando...</span>' : ''}
      </div></td>`;
    tb.appendChild(tr);
  }
}

async function showTunerDetail(rid){
  const r=await fetch('/api/tuner/requests/'+encodeURIComponent(rid));
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ alert('Erro: '+(j.error||'falha')); return; }
  const req=j.request;
  const det=$('#tunerDetail');
  const br=req.briefing||{};
  const iters=req.iterations||[];
  let html=`<div class="kv"><b>ID</b><span class="mono">${escapeHtml(req.id)}</span></div>
    <div class="kv"><b>Estado</b><span class="pill">${escapeHtml(req.status)}</span> ${req.recommendation?`<span class="pill ${req.recommendation==='ativar'?'match':'warn'}">${escapeHtml(req.recommendation)}</span>`:''}</div>
    <div class="kv"><b>Rascunho</b><span class="mono">${escapeHtml(req.draft_product_id)}</span> ${req.draft_product_current ? (req.draft_product_current.draft ? '<span class="pill warn">rascunho</span>' : (req.draft_product_current.enabled?'<span class="pill match">ativo</span>':'<span class="pill off">desativado</span>')) : ''} ${req.draft_product_current && req.draft_product_current.draft ? '<span class="small muted">draft=true</span>' : ''}</div>
    <hr>
    <b>Briefing</b><div class="small mono" style="white-space:pre-wrap;background:#0b0e13;border:1px solid var(--border);padding:8px;border-radius:8px;margin-top:6px">${escapeHtml(JSON.stringify(br,null,2))}</div>`;
  if(iters.length){
    html+=`<hr><b>Evolução por iteração</b>`;
    for(const it of iters){
      // llm badge per iteration
      let llmHtml = '';
      if(it.llm){
        const llm = it.llm;
        if(llm.used){
          llmHtml = `<span class="pill match">✓ LLM ${escapeHtml(llm.model||llm.provider||'')} ${llm.latency_ms? llm.latency_ms+'ms':''}</span> ${llm.prompt_tokens!=null? `<span class="small muted">tok ${llm.prompt_tokens}/${llm.completion_tokens}</span>`:''}`;
        } else {
          llmHtml = `<span class="pill off">heurística</span> <span class="small muted">${escapeHtml((llm.error||'fallback').slice(0,60))}</span>`;
        }
      } else {
        llmHtml = '<span class="small muted">sem LLM (legado)</span>';
      }
      html+=`<div style="border:1px solid var(--border);border-radius:8px;padding:10px;margin-top:8px;background:#0b0e13">
        <div style="font-weight:700">Iteração ${it.n} — precisão ${(it.precision*100).toFixed(1)}% · ${it.tp_count} TP / ${it.fp_count} FP · ${it.total_cards} cards, ${it.matched} matched — ${llmHtml}</div>
        <div class="small muted">queries: ${escapeHtml((it.queries||[]).join(', '))} · include: <span class="mono">${escapeHtml(it.include_pattern||'—')}</span> · exclude: <span class="mono">${escapeHtml((it.exclude_keywords||[]).join(', ')||'—')}</span> · preço ${it.min_price??'—'}..${it.max_price??'∞'}</div>`;
      if(it.classification){
        const cl = it.classification;
        html+=`<div class="small muted">classificação: TP ${cl.tp} / FP ${cl.fp} · precisão ${(cl.precision*100).toFixed(1)}%</div>`;
      }
      if(it.examples_tp?.length){
        html+=`<div class="small" style="margin-top:6px"><b>Exemplos TP</b><ul style="margin:4px 0;padding-left:18px">`;
        for(const ex of it.examples_tp) html+=`<li>${escapeHtml(ex.title)} — R$ ${ex.price??'—'} <a href="${escapeAttr(ex.url||'#')}" target="_blank">↗</a></li>`;
        html+=`</ul></div>`;
      }
      if(it.examples_fp?.length){
        html+=`<div class="small" style="margin-top:6px"><b>Falsos positivos</b><ul style="margin:4px 0;padding-left:18px">`;
        for(const ex of it.examples_fp) html+=`<li>${escapeHtml(ex.title)} — <span class="muted">${escapeHtml(ex.motivo||ex.reason||'')}</span> → <span class="mono">${escapeHtml(ex.sugestao||'')}</span></li>`;
        html+=`</ul></div>`;
      }
      const ref = it.refinement || it.proposed;
      if(ref){
        const p=ref;
        html+=`<div class="small" style="margin-top:6px"><b>Refinamento proposto antes de aplicar:</b> ${p.add_exclude?.length ? `+exclude ${escapeHtml(p.add_exclude.join(', '))}` : ''} ${p.new_include_pattern? `· novo include <span class="mono">${escapeHtml(p.new_include_pattern)}</span>`:''} ${p.reason? `· <span class="muted">${escapeHtml(p.reason)}</span>`:''} ${it.applied?' <span class="pill match">aplicado</span>':''}</div>`;
      }
      if(it.error) html+=`<div class="small" style="color:var(--err)">erro: ${escapeHtml(it.error)}</div>`;
      if(it.llm && it.llm.error) html+=`<div class="small" style="color:var(--warn)">LLM erro: ${escapeHtml(it.llm.error.slice(0,200))}</div>`;
      html+=`</div>`;
    }
  } else {
    html+=`<div class="small muted" style="margin-top:8px">Ainda sem iterações — pedido em fila/pendente.</div>`;
  }
  if(req.final_report){
    const fr=req.final_report;
    html+=`<hr><b>Relatório consolidado</b>
      <div class="small muted">Recomendação: <span class="pill ${fr.recomendacao==='ativar'?'match':'warn'}">${escapeHtml(fr.recomendacao)}</span> · precisão final ${(fr.precision_final*100).toFixed(1)}% · ${fr.total_tp} TP / ${fr.total_fp} FP</div>
      <div class="small" style="margin-top:6px"><b>Config final</b><pre class="small mono" style="white-space:pre-wrap;background:#0b0e13;border:1px solid var(--border);padding:8px;border-radius:8px">${escapeHtml(JSON.stringify(fr.config_final,null,2).slice(0,2000))}</pre></div>
      <div class="small" style="margin-top:6px"><b>Ofertas finais relevantes</b><ul style="padding-left:18px">`;
    for(const o of (fr.ofertas_finais||[]).slice(0,10)){
      html+=`<li>${escapeHtml(o.title)} — R$ ${o.price??'—'} <a href="${escapeAttr(o.url||'#')}" target="_blank">↗</a></li>`;
    }
    html+=`</ul></div>
      <div class="row" style="margin-top:10px">
        <button class="btn primary" onclick="tunerAction('${escapeAttr(req.id)}','activate')">✅ Ativar (habilita e agenda)</button>
        <button class="btn" onclick="tunerAction('${escapeAttr(req.id)}','keep_draft')">Manter como rascunho</button>
        <button class="btn danger" onclick="tunerAction('${escapeAttr(req.id)}','discard')">🗑️ Descartar</button>
        <a class="btn small" href="/api/deals?product_id=${escapeAttr(req.draft_product_id)}&matched_only=1" target="_blank">Ver deals do rascunho ↗</a>
      </div>`;
  }
  det.innerHTML=html;
  // scroll to detail
  det.scrollIntoView({behavior:'smooth', block:'start'});
}

async function tunerAction(rid, action){
  if(action==='discard' && !confirm('Descartar rascunho? O produto draft será removido.')) return;
  if(action==='activate' && !confirm('Ativar produto? Ele passará a ser habilitado e agendado, aparecendo em coletas gerais e podendo notificar no Telegram.')) return;
  const r=await fetch('/api/tuner/requests/'+encodeURIComponent(rid)+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ alert('Erro: '+(j.error||j.errors?.join('; ')||'falha')); return; }
  alert('Ação '+action+' OK — novo estado: '+j.request.status);
  loadTunerRequests();
  showTunerDetail(rid);
  loadProducts();
}
let tunerModalDraft={sementes:[]};

function openTunerModal(){
  tunerModalDraft.sementes=[];
  $('#tunerDesc').value='';
  $('#tunerDefinicao').value='';
  $('#tunerObrig').value='';
  $('#tunerExcl').value='';
  $('#tunerPrecoMin').value='';
  $('#tunerPrecoMax').value='';
  $('#tunerSementesChips').innerHTML='<span class="muted small">adicione 2-3 termos</span>';
  $('#tunerSementesInput').value='';
  $('#tunerStatus').textContent='';
  $('#tunerModal').classList.add('open');
}

function closeTunerModal(){ $('#tunerModal').classList.remove('open'); }

function renderTunerSementes(){
  const c=$('#tunerSementesChips'); c.innerHTML='';
  tunerModalDraft.sementes.forEach((s,i)=>{
    const d=document.createElement('span'); d.className='query-chip'; d.innerHTML=`${escapeHtml(s)} <button onclick="removeTunerSemente(${i})">×</button>`; c.appendChild(d);
  });
  if(!tunerModalDraft.sementes.length) c.innerHTML='<span class="muted small">adicione 2-3 termos que digitaria na OLX</span>';
}

function addTunerSemente(){
  const v=$('#tunerSementesInput').value.trim(); if(!v) return;
  if(tunerModalDraft.sementes.length>=3){ $('#tunerStatus').textContent='Máximo 3 termos (2–3)'; return; }
  if(v.length<2 || v.length>80){ $('#tunerStatus').textContent='Termo deve ter 2..80 chars'; return; }
  tunerModalDraft.sementes.push(v); $('#tunerSementesInput').value=''; renderTunerSementes();
}

function removeTunerSemente(i){ tunerModalDraft.sementes.splice(i,1); renderTunerSementes(); }

async function submitTuner(){
  const payload={
    descricao: $('#tunerDesc').value.trim(),
    definicao: $('#tunerDefinicao').value.trim(),
    obrigatorios: $('#tunerObrig').value.trim(),
    excludentes: $('#tunerExcl').value.trim(),
    preco_min: $('#tunerPrecoMin').value.trim()? parseFloat($('#tunerPrecoMin').value) : null,
    preco_max: $('#tunerPrecoMax').value.trim()? parseFloat($('#tunerPrecoMax').value) : null,
    sementes: tunerModalDraft.sementes,
  };
  // limpa nulls vazios
  if(!payload.definicao) delete payload.definicao;
  if(!payload.obrigatorios) delete payload.obrigatorios;
  if(!payload.excludentes) delete payload.excludentes;
  if(payload.preco_min==null || isNaN(payload.preco_min)) delete payload.preco_min;
  if(payload.preco_max==null || isNaN(payload.preco_max)) delete payload.preco_max;
  if(!payload.descricao){ $('#tunerStatus').textContent='Descrição obrigatória (1..200)'; return; }
  if(!payload.definicao){ $('#tunerStatus').textContent='Definição obrigatória (1 frase)'; return; }
  if(payload.sementes.length < 2){ $('#tunerStatus').textContent='Informe ao menos 2 sementes (2..3)'; return; }
  if(payload.sementes.length > 3){ $('#tunerStatus').textContent='Máximo 3 sementes'; return; }
  if(payload.sementes.some(s=>s.length<2||s.length>80)){ $('#tunerStatus').textContent='Cada semente 2..80 chars'; return; }
  $('#tunerStatus').textContent='Enviando...';
  const r=await fetch('/api/tuner/requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ $('#tunerStatus').textContent='Erro: '+(j.errors||[j.error]).join('; '); return; }
  $('#tunerStatus').textContent='✓ Pedido criado: '+j.request.id+' — refinamento em andamento';
  closeTunerModal();
  loadTunerRequests();
  setTimeout(()=>showTunerDetail(j.request.id), 600);
}
// --- Tuner LLM config (critério 8) ---
let tunerModelsCache=[];
let tunerModelsAll=[];

async function loadTunerLlmConfig(){
  const r=await fetch('/api/config/llm');
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ $('#tunerLlmStatus').textContent='Erro ao carregar LLM: '+(j.error||j.errors?.join('; ')); return; }
  const llm=j.effective||j.llm||{};
  $('#tunerProvider').value=llm.provider||'openrouter';
  $('#tunerBaseUrl').value=llm.base_url||'';
  $('#tunerApiKey').value=llm.api_key||'';
  $('#tunerModelInput').value=llm.model||'';
  // sincroniza select se já tiver modelos
  if(tunerModelsCache.length){
    const sel=$('#tunerModelSelect');
    sel.value=llm.model||'';
    if(!sel.value && llm.model) {
      // adiciona opção faltante
      const o=document.createElement('option'); o.value=llm.model; o.textContent=llm.model+' (atual)'; sel.appendChild(o); sel.value=llm.model;
    }
  }
  $('#tunerLlmStatus').textContent='✓ Config carregada ('+llm.provider+' @ '+(llm.base_url||'default')+')';
  if(j.env_override && j.env_override.length){
    $('#tunerEnvBadge').textContent='⚠️ Env override ativo: '+j.env_override.join(', ')+' (tem precedência; salvar arquivo não afeta até remover env)';
    $('#tunerEnvBadge').style.color='var(--warn, #f59e0b)';
  } else {
    $('#tunerEnvBadge').textContent='';
  }
  // se já tem modelos em cache, filtra
  if(!tunerModelsAll.length) loadTunerModels();
}

async function saveTunerLlmConfig(){
  const payload={
    provider: $('#tunerProvider').value,
    base_url: $('#tunerBaseUrl').value.trim(),
    model: $('#tunerModelInput').value.trim(),
    api_key: $('#tunerApiKey').value.trim(),
  };
  if(!payload.model){ $('#tunerLlmStatus').textContent='Modelo obrigatório'; return; }
  // se api_key veio mascarada *** -> envia como está para backend preservar
  // se vazio, envia vazio para limpar (permitido para opencode)
  $('#tunerLlmStatus').textContent='Salvando...';
  const r=await fetch('/api/config/llm',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ $('#tunerLlmStatus').textContent='Erro: '+(j.errors?.join('; ')||j.error||'falha'); return; }
  $('#tunerLlmStatus').textContent='✓ Salvo: '+j.llm.provider+' — '+j.llm.model;
  // recarrega para exibir mascaramento
  setTimeout(loadTunerLlmConfig, 400);
}

async function loadTunerModels(){
  const prov=$('#tunerProvider').value;
  const bu=$('#tunerBaseUrl').value.trim();
  const params=new URLSearchParams();
  if(prov) params.set('provider', prov);
  if(bu) params.set('base_url', bu);
  $('#tunerLlmStatus').textContent='Carregando modelos...';
  const r=await fetch('/api/tuner/models?'+params.toString());
  const j=await r.json().catch(()=>({}));
  if(j.ok){
    tunerModelsAll=j.models||[];
    tunerModelsCache=tunerModelsAll.slice();
    const sel=$('#tunerModelSelect');
    sel.innerHTML='';
    if(!tunerModelsAll.length){
      sel.innerHTML='<option value="">(nenhum modelo retornado)</option>';
      $('#tunerLlmStatus').textContent='⚠️ Nenhum modelo retornado ('+j.provider+' @ '+j.base_url+') — use input livre';
    } else {
      sel.innerHTML='<option value="">— selecione —</option>';
      for(const m of tunerModelsAll){
        const o=document.createElement('option'); o.value=m.id; o.textContent=m.id+(m.name&&m.name!==m.id ? ' — '+m.name : ''); sel.appendChild(o);
      }
      // tenta selecionar atual
      const cur=$('#tunerModelInput').value.trim();
      if(cur) sel.value=cur;
      $('#tunerLlmStatus').textContent='✓ '+j.models.length+' modelos ('+j.provider+')'+(j.cached?' [cache]':'');
    }
  } else {
    const err=j.error||'falha';
    $('#tunerLlmStatus').textContent='✗ Modelos: '+err+' — use input livre';
    if(j.models && j.models.length){
      tunerModelsAll=j.models;
      const sel=$('#tunerModelSelect');
      sel.innerHTML='<option value="">— cache —</option>';
      for(const m of tunerModelsAll){ const o=document.createElement('option'); o.value=m.id; o.textContent=m.id; sel.appendChild(o); }
    }
  }
}

function filterTunerModels(){
  const q=$('#tunerModelFilter').value.trim().toLowerCase();
  const sel=$('#tunerModelSelect');
  const cur=sel.value;
  sel.innerHTML='';
  if(!q){
    sel.innerHTML='<option value="">— selecione —</option>';
    for(const m of tunerModelsAll){ const o=document.createElement('option'); o.value=m.id; o.textContent=m.id; sel.appendChild(o); }
  } else {
    const filtered=tunerModelsAll.filter(m=>m.id.toLowerCase().includes(q));
    sel.innerHTML='<option value="">— filtrado ('+filtered.length+') —</option>';
    for(const m of filtered){ const o=document.createElement('option'); o.value=m.id; o.textContent=m.id; sel.appendChild(o); }
  }
  if(cur) sel.value=cur;
}

function onTunerProviderChange(){
  const p=$('#tunerProvider').value;
  const buInput=$('#tunerBaseUrl');
  if(p==='openrouter' && !buInput.value.trim()) buInput.value='https://openrouter.ai/api/v1';
  else if(p==='opencode' && !buInput.value.trim()) buInput.value='http://127.0.0.1:4096/v1';
  else if(p==='mock') buInput.value='';
  // limpa status para forçar reload modelos
  $('#tunerLlmStatus').textContent='';
}

function onTunerModelSelect(){
  const v=$('#tunerModelSelect').value;
  if(v) $('#tunerModelInput').value=v;
}




// init
loadProducts(); loadDeals();
autoRefreshTimer=setInterval(()=>{ if(document.getElementById('tab-results').style.display!=='none') loadDeals(); }, 30000);
setInterval(async ()=>{ await loadSchedules(); renderProducts(); renderScheduleStatus(); renderSchedulesTable(); }, 15000);
fetch('/api/scrape/status').then(r=>r.json()).then(j=>{ if(j.running) { switchTab('scrape'); startPolling(); }});
document.addEventListener('DOMContentLoaded', ()=>{ toggleScheduleFields(); });
