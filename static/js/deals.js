// static/js/deals.js — aba Resultados + Manutenção/Expurgo + export (Fase 6)

function debouncedLoad(){ clearTimeout(debounceTimer); debounceTimer=setTimeout(loadDeals, 350); }

async function loadDeals(){
  const reEval = document.getElementById('reEvaluate')?.checked ? '1' : '';
  const adherentSel = document.getElementById('adherentFilter')?.value || 'all';
  const params = new URLSearchParams({
    product_id: $('#filterProduct').value,
    q: $('#qSearch').value.trim(),
    matched_only: $('#matchedOnly').checked ? '1' : '',
    notified: $('#hideNotified')&&$('#hideNotified').checked ? '0' : '',
    sort: $('#sortBy').value,
    order: $('#sortOrder').value,
    price_min: ($('#filterPriceMin')?.value||'').trim(),
    price_max: ($('#filterPriceMax')?.value||'').trim(),
    limit: dealsLimit,
  });
  if(reEval) params.set('re-evaluate','1');
  if(reEval && adherentSel && adherentSel!=='all') params.set('adherent', adherentSel);
  for(const [k,v] of [...params]) if(!v) params.delete(k);
  const pmin = parseFloat(params.get('price_min')), pmax = parseFloat(params.get('price_max'));
  if(!isNaN(pmin) && !isNaN(pmax) && pmin >= pmax){
    $('#dealStats').textContent='Faixa de preço inválida: min deve ser < max';
    return;
  }
  const r = await fetch('/api/deals?'+params);
  if(r.status===400){ const j=await r.json().catch(()=>({})); $('#dealStats').textContent=j.error||j.errors?.join('; ')||'erro 400'; return; }
  const j=await r.json();
  dealsCache=j.deals||[];
  const matchedOnly = $('#matchedOnly').checked?'1':'0';
  const notifiedParam = $('#hideNotified')&&$('#hideNotified').checked?'0':'all';
  const extra = reEval ? ` (re-avaliado ${adherentSel!=='all' ? (adherentSel==='0'?'não-aderentes':'aderentes') : ''})` : '';
  $('#dealStats').textContent = `${j.total} anúncios ${matchedOnly==='1'?'(só matches)':''}${notifiedParam==='0'?' (só não notificados)':''}${extra}${params.get('price_min')||params.get('price_max')?` (R$ ${params.get('price_min')||'-'} → R$ ${params.get('price_max')||'∞'})`:''} — exibindo ${j.count}`;
  $('#resultCountBadge').textContent = `(${j.total})`;
  $('#limitInfo').textContent = j.count < j.total ? `${j.total-j.count} ocultos — clique para carregar mais` : '';
  renderDeals();
}

function renderDeals(){
  const tb=$('#dealsBody');
  if(!dealsCache.length){ tb.innerHTML=`<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">nenhum resultado${$('#qSearch').value?' para filtro':''} — tente desmarcar "só matches" ou trocar produto</td></tr>`; return; }
  tb.innerHTML='';
  for(const d of dealsCache){
    const tr=document.createElement('tr');
    const price = d.price!=null ? `R$ ${d.price.toLocaleString('pt-BR',{minimumFractionDigits:2})}` : '—';
    const dt = d.fetched_at ? new Date(d.fetched_at).toLocaleString('pt-BR') : '—';
    const prodName = d.product_name || d.product_id || '—';
    const statusBadge = d.matched
      ? '<span class="pill match">✓ match</span>'
      : (d.discard_reason ? `<span class="pill off" title="descartado por ${escapeHtml(d.discard_reason)}">${escapeHtml(d.discard_reason)}</span>` : '<span class="pill off">descartado</span>');
    let curBadge='';
    if(d.matched_current!==undefined){
      if(d.matched_current===false && d.matched===true) curBadge='<span class="pill off" title="Não-aderente com filtros atuais (expurgável)">⚠️ não-aderente</span> <span class="small muted mono">'+escapeHtml(d.discard_reason_current||'')+'</span>';
      else if(d.matched_current===true) curBadge='<span class="pill match">✓ aderente</span>';
      else if(d.matched_current===false) curBadge='<span class="pill off">'+escapeHtml(d.discard_reason_current||'não-aderente')+'</span>';
      if(d.discard_reason_current && d.discard_reason_current.startsWith('orphan')) curBadge='<span class="pill off" style="background:rgba(150,150,150,.2)">🏚️ órfão</span> '+curBadge;
    }
    tr.innerHTML = `
      <td><span class="tag">${escapeHtml(prodName)}</span><div class="small mono muted">${escapeHtml(d.product_id||'')}</div></td>
      <td>
        <div style="font-weight:600">${escapeHtml(d.title||'')}</div>
        <div class="small muted mono">${d.id||''} · ${escapeHtml(d.query||'')} p${d.page||''}</div>
        ${statusBadge} ${curBadge}
        ${d.notified?'<span class="pill match" title="Já enviado ao Telegram">✈️ notificado</span>':'<span class="pill off">não notificado</span>'}
      </td>
      <td class="mono" style="font-weight:700">${price}</td>
      <td><span class="tag">${escapeHtml(d.query||'')}</span></td>
      <td class="small mono">${dt}</td>
      <td><a href="${escapeAttr(d.url||'#' )}" target="_blank" rel="noopener">abrir ↗</a>
        <button class="btn small" style="margin-top:4px;display:block" title="Remove do registro de notificados e reenvia agora" onclick="renotifyDeal('${escapeAttr(d.product_id||'')}','${escapeAttr(d.id||'')}')">↻ Reenviar</button></td>`;
    tb.appendChild(tr);
  }
}

async function renotifyDeal(pid, adId){
  const r=await fetch('/api/deals/renotify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product_id:pid, ad_id:adId})});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ alert('Erro: '+(j.error||'falha')); return; }
  const c=j.counters||{};
  const parts=[`${c.enviados||0} enviado(s)`, `${c.pulados||0} ignorado(s)`, `${c.re_notificados||0} re-notificado(s)`];
  if(c.erro) parts.push('erro: '+c.erro);
  alert(`Reenvio de ${j.key}:\n`+parts.join('\n'));
  loadDeals();
}

async function clearNotified(){
  if(!confirm('Limpar registro de notificações (~/.olx/notified.json)?\n\nAnúncios existentes em deals.json poderão ser re-enviados no próximo scrape.')) return;
  const r=await fetch('/api/notified',{method:'DELETE'});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ alert('Erro: '+(j.error||'falha')); return; }
  alert(`Registro limpo: ${j.cleared} entrada(s) removida(s).`);
  loadDeals();
}
// --- Purge / Manutenção (Fase 4) ---

function refreshPurgeProducts(){
  const c=$('#purgeProducts');
  if(!c) return;
  c.innerHTML='';
  if(!products.length){ c.innerHTML='<span class="muted">Nenhum produto</span>'; return; }
  // lista produtos atuais + órfãos detectados via último preview
  for(const p of products){
    const label=document.createElement('label');
    label.style.cssText='display:flex;gap:6px;align-items:center;text-transform:none;letter-spacing:0;font-weight:500;color:var(--fg);margin:4px 0';
    label.innerHTML=`<input type="checkbox" value="${escapeAttr(p.id)}" style="width:auto"> ${escapeHtml(p.name)} <span class="mono muted">(${escapeHtml(p.id)})</span>`;
    c.appendChild(label);
  }
}

function getPurgeProductIds(){
  const checked=[...document.querySelectorAll('#purgeProducts input:checked')].map(cb=>cb.value);
  return checked.length? checked : null;
}

function getPurgeReasons(){
  const checked=[...document.querySelectorAll('#purgeReasons input:checked')].map(cb=>cb.value);
  return checked.length? checked : null;
}

async function doPurgePreview(){
  const body={dry_run:true};
  const pids=getPurgeProductIds();
  if(pids) body.product_ids=pids;
  const reasons=getPurgeReasons();
  if(reasons) body.reasons=reasons;
  if($('#purgeIncludeOrphans').checked) body.include_orphans=true;
  const status=$('#purgeStatus').value;
  if(status && status!=='all') body.status=status;
  const older=$('#purgeOlderThan').value.trim();
  if(older) body.older_than=older;
  $('#purgeStatusMsg').textContent='Carregando preview...';
  try{
    const r=await fetch('/api/deals/purge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok){ $('#purgeStatusMsg').textContent='Erro: '+(j.error||j.errors?.join('; ')||'falha'); return; }
    $('#purgePreview').textContent=JSON.stringify({total_candidates:j.total_candidates, by_product:j.by_product, by_reason:j.by_reason, notified_entries_affected:j.notified_entries_affected}, null, 2);
    $('#purgeStatusMsg').textContent=`Preview: ${j.total_candidates} candidatos` + (j.preview_truncated?' (truncado 200)':'');
    const tb=$('#purgeCandidatesBody');
    tb.innerHTML='';
    const cand=j.candidates||[];
    if(!cand.length) tb.innerHTML='<tr><td colspan="3" class="muted" style="text-align:center;padding:12px">nenhum</td></tr>';
    else for(const d of cand.slice(0,20)){
      const tr=document.createElement('tr');
      // tenta deduzir motivo se não veio detalhado: usa by_reason? mostra discard_reason_current se existir via preview? candidatos já têm matched_current? não, mas podemos mostrar product
      tr.innerHTML=`<td class="mono">${escapeHtml(d.product_id||'')}</td><td>${escapeHtml((d.title||'').slice(0,80))}</td><td class="small mono">${escapeHtml(d.discard_reason||d.matched?'':'')}</td>`;
      tb.appendChild(tr);
    }
    // atualiza órfãos na lista se houver by_product com ids não em products
    if(j.by_product){
      for(const pid of Object.keys(j.by_product)){
        if(!products.some(p=>p.id===pid)){
          // órfão detectado, adiciona checkbox temporário se não existe
          const exists=[...document.querySelectorAll('#purgeProducts input')].some(cb=>cb.value===pid);
          if(!exists){
            const label=document.createElement('label');
            label.style.cssText='display:flex;gap:6px;align-items:center;text-transform:none;letter-spacing:0;font-weight:500;color:var(--fg);margin:4px 0';
            label.innerHTML=`<input type="checkbox" value="${escapeAttr(pid)}" style="width:auto"> ${escapeHtml(pid)} <span class="mono muted">(órfão · ${j.by_product[pid]})</span>`;
            document.getElementById('purgeProducts').appendChild(label);
          }
        }
      }
    }
  }catch(e){ $('#purgeStatusMsg').textContent='Erro: '+e; }
}

async function doPurgeExec(){
  const pids=getPurgeProductIds();
  const reasons=getPurgeReasons();
  let preview= {total_candidates: '?', by_product:{}, by_reason:{}};
  try{
    const preBody={dry_run:true};
    if(pids) preBody.product_ids=pids;
    if(reasons) preBody.reasons=reasons;
    if($('#purgeIncludeOrphans').checked) preBody.include_orphans=true;
    const st=$('#purgeStatus').value;
    if(st && st!=='all') preBody.status=st;
    const older=$('#purgeOlderThan').value.trim();
    if(older) preBody.older_than=older;
    const rPre=await fetch('/api/deals/purge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(preBody)});
    const jPre=await rPre.json();
    if(rPre.ok) preview=jPre;
  }catch{}
  const msg=`Expurgar ${preview.total_candidates ?? '?'} ofertas?` + `\nby_product: ${JSON.stringify(preview.by_product||{})}` + `\nby_reason: ${JSON.stringify(preview.by_reason||{})}` + `\nSerá criado backup em deals.json.bak-purge-<timestamp>\n\nConfirmar?`;
  if(!confirm(msg)) return;
  const body={confirm:true};
  if(pids) body.product_ids=pids;
  if(reasons) body.reasons=reasons;
  if($('#purgeIncludeOrphans').checked) body.include_orphans=true;
  const status=$('#purgeStatus').value;
  if(status && status!=='all') body.status=status;
  const older=$('#purgeOlderThan').value.trim();
  if(older) body.older_than=older;
  $('#purgeStatusMsg').textContent='Executando expurgo...';
  const r=await fetch('/api/deals/purge',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){ $('#purgeStatusMsg').textContent='Erro: '+(j.error||'falha'); alert('Erro: '+(j.error||'falha')); return; }
  $('#purgeStatusMsg').textContent=`✓ Purgados ${j.purged}, restantes ${j.remaining}, backup: ${j.backup_path}`;
  $('#purgePreview').textContent=JSON.stringify(j,null,2);
  alert(`Purgados ${j.purged}, restantes ${j.remaining}\nBackup: ${j.backup_path}`);
  loadDeals(); loadProducts(); refreshPurgeProducts();
}

function refreshPurgePreview(){ refreshPurgeProducts(); doPurgePreview(); }

function loadMore(){ dealsLimit+=80; loadDeals(); }

// --- Tuner (Backlog #12) ---
let tunerRequests=[];
let tunerDebounce=null;

function exportJSON(){
  const blob=new Blob([JSON.stringify(dealsCache,null,2)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='olx-deals.json'; a.click();
}

function exportCSV(){
  const rows=[['product_id','product_name','id','title','price','query','page','fetched_at','url','matched','discard_reason']];
  for(const d of dealsCache) rows.push([d.product_id,d.product_name,d.id,d.title,d.price,d.query,d.page,d.fetched_at,d.url,d.matched,d.discard_reason]);
  const csv=rows.map(r=>r.map(v=>`"${String(v??'').replace(/"/g,'""')}"`).join(',')).join('\n');
  const blob=new Blob([csv],{type:'text/csv'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='olx-deals.csv'; a.click();
}
