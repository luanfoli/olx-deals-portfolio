// static/js/api.js — wrapper fetch + X-API-Token + globals + health + formatos (Fase 6)
// Carregado primeiro; expõe window.$, fetch wrapper e estado global compartilhado.

const $ = s => document.querySelector(s);

// Auth opt-in: se o servidor exigir X-API-Token, anexa o token de localStorage
// e, em 401, pede o token uma única vez (prompts concorrentes compartilham o mesmo prompt) e repete a chamada.
const _origFetch = window.fetch.bind(window);
let _tokenPrompt = null;
window.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  if (!url.startsWith('/api/') || url.startsWith('/api/health')) return _origFetch(input, init);
  const headers = new Headers((init && init.headers) || (input && input.headers) || {});
  const saved = localStorage.getItem('olx_api_token');
  if (saved && !headers.has('X-API-Token')) headers.set('X-API-Token', saved);
  let r = await _origFetch(input, Object.assign({}, init, {headers}));
  if (r.status === 401) {
    if (!_tokenPrompt) _tokenPrompt = Promise.resolve(prompt('Token da API (X-API-Token):'));
    const t = await _tokenPrompt.finally(() => { _tokenPrompt = null; });
    if (t !== null && t !== '') {
      localStorage.setItem('olx_api_token', t);
      headers.set('X-API-Token', t);
      r = await _origFetch(input, Object.assign({}, init, {headers}));
    }
  }
  return r;
};

let products = [];
let cfg = null;
let editingId = null;
let dealsCache = [];
let dealsLimit = 80;
let pollTimer = null;
let autoRefreshTimer = null;
let lastLogLen = 0;
let scheduleById = {};
let scheduleQueue = [];
let scheduleMeta = {scheduler_running:false, has_scheduler:false};


function switchTab(name){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.toggle('active', b.dataset.tab===name));
  for(const id of ['products','scrape','results','schedules','tuner','maintenance']){
    const el=document.getElementById('tab-'+id);
    if(el) el.style.display = (id===name?'':'none');
  }
  if(name==='results') loadDeals();
  if(name==='products') loadProducts();
  if(name==='scrape') loadProductsForScrape();
  if(name==='schedules') loadSchedulesAndRender();
  if(name==='tuner'){ loadTunerRequests(); loadTunerLlmConfig(); }
  if(name==='maintenance') refreshPurgePreview();
}
document.querySelectorAll('.tab-btn').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));


async function refreshHealth(){
  try{
    const r = await fetch('/api/health'); await r.json();
    $('#health').textContent='● online'; $('#health').className='badge ok';
  }catch{ $('#health').textContent='offline'; $('#health').className='badge err'; }
  try{
    const r=await fetch('/api/cookies/status'); const j=await r.json();
    if(!j.exists){ $('#cookiesBadge').textContent='cookies: ausente'; $('#cookiesBadge').className='badge warn'; }
    else if(j.age_minutes>30){ $('#cookiesBadge').textContent=`cookies: ${j.age_minutes} min`; $('#cookiesBadge').className='badge warn'; }
    else { $('#cookiesBadge').textContent=`cookies: ok (${j.age_minutes} min)`; $('#cookiesBadge').className='badge ok'; }
    $('#cookiesInfo').textContent = JSON.stringify(j, null, 2);
  }catch(e){ $('#cookiesBadge').textContent='cookies: erro'; }
}
setInterval(refreshHealth, 30000); refreshHealth();


function formatInterval(mins){
  if(mins==null || mins==='') return '—';
  mins=parseInt(mins);
  if(mins < 60) return `a cada ${mins}min`;
  if(mins % 60 === 0) return `a cada ${mins/60}h`;
  if(mins % 1440 === 0) return `a cada ${mins/1440}d`;
  return `a cada ${mins}min`;
}

function formatNextRun(iso){
  if(!iso) return '—';
  try{
    const d = new Date(iso);
    return d.toLocaleString('pt-BR', {hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit'});
  }catch{return iso}
}

function formatLastRun(iso){
  if(!iso) return '<span class="muted">nunca</span>';
  try{
    const d=new Date(iso);
    const s=d.toLocaleString('pt-BR', {hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit'});
    const diffMs=Date.now()-d.getTime();
    const mins=Math.round(diffMs/60000);
    let ago='';
    if(mins<1) ago='agora';
    else if(mins<60) ago=`há ${mins}min`;
    else if(mins<1440) ago=`há ${Math.round(mins/60)}h`;
    else ago=`há ${Math.round(mins/1440)}d`;
    return `${s} <span class="muted">(${ago})</span>`;
  }catch{return iso}
}

function escapeHtml(s){ return (s||'').replace(/[&<>"']/g, m=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m])); }

function escapeAttr(s){ return s.replace(/"/g,'&quot;'); }
