const state = { category: 'en_vivo', matches: [], query: '', league: '', revision: -1, active: false, browsing: false, data: null, request: 0 };
const savedMatches = () => {try { const rows=JSON.parse(localStorage.getItem('football.favorites')||'[]');return Array.isArray(rows)?rows:[] } catch {return []}};
const matchKey = p => `${p.modo}:${p.fixture_id||p.id}`;
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const finite = (value, fallback = 0) => value != null && Number.isFinite(Number(value)) ? Number(value) : fallback;
const pct = (value) => value == null ? '—' : `${finite(value).toFixed(1)}%`;

async function api(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', headers: {'Content-Type': 'application/json'}, ...options});
  let payload;
  try { payload = await response.json(); } catch { payload = {ok: false, error: 'Respuesta inválida del servidor'}; }
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `Error HTTP ${response.status}`);
  return payload;
}

function setConnection(kind, text) {
  const el = $('connection');
  el.className = `connection ${kind}`;
  el.querySelector('span').textContent = text;
}

function setMatchMessage(text, error = false) {
  const el = $('matchMessage');
  el.textContent = text;
  el.className = `message${error ? ' error' : ''}`;
}

async function loadMatches() {
  const request = ++state.request;
  const category = state.category;
  $('refreshMatches').classList.add('loading');
  setMatchMessage(state.category === 'en_vivo' ? 'Consultando partidos en vivo…' : 'Cargando partidos…');
  $('matches').innerHTML = '<div class="empty">Actualizando la lista…</div>';
  try {
    const data = await api(`/api/partidos?categoria=${encodeURIComponent(category)}`);
    if(request !== state.request)return;
    state.matches = data.partidos || [];
    if(category==='guardados')state.matches=[...new Map([...state.matches,...savedMatches()].map(p=>[matchKey(p),p])).values()];
    const leagues=[...new Set(state.matches.map(p=>p.liga).filter(Boolean))].sort();
    $('leagueFilter').innerHTML='<option value="">Todas las competiciones</option>'+leagues.map(l=>`<option value="${esc(l)}">${esc(l)}</option>`).join('');
    if(!leagues.includes(state.league))state.league='';
    $('leagueFilter').value=state.league;
    renderMatches();
    setMatchMessage(`${state.matches.length} partido${state.matches.length === 1 ? '' : 's'} disponible${state.matches.length === 1 ? '' : 's'}`);
  } catch (error) {
    if(request !== state.request)return;
    state.matches = [];
    $('matches').innerHTML = `<div class="empty">No se pudo cargar esta categoría.<br><small>${esc(error.message)}</small></div>`;
    setMatchMessage(error.message, true);
  } finally {
    if(request === state.request)$('refreshMatches').classList.remove('loading');
  }
}

function renderMatches() {
  const query = state.query.trim().toLocaleLowerCase('es');
  const rows = state.matches.filter(p => (!state.league||p.liga===state.league)&&(!query || `${p.nombre} ${p.liga}`.toLocaleLowerCase('es').includes(query)));
  const favorites = new Set(savedMatches().map(matchKey));
  if (!rows.length) {
    $('matches').innerHTML = `<div class="empty">${query ? 'No hay coincidencias.' : 'No hay partidos en esta categoría ahora.'}</div>`;
    return;
  }
  $('matches').innerHTML = rows.map((p, index) => `
    <article class="match-card">
      <div>
        <h3>${esc(p.nombre)}</h3>
        <p>${esc(p.liga || 'Competición sin identificar')}</p>
        <div class="match-meta"><span class="status-tag">${esc(p.estado || p.hora || 'Programado')}</span><span class="match-score">${esc(p.marcador || '—')}</span></div>
      </div>
      <div class="match-actions"><button class="favorite-btn" data-favorite="${index}" aria-label="${favorites.has(matchKey(p))?'Quitar de':'Añadir a'} favoritos" aria-pressed="${favorites.has(matchKey(p))}">${favorites.has(matchKey(p))?'★':'☆'}</button><button class="analyze-btn" data-match-index="${index}" type="button">Analizar ↗</button></div>
    </article>`).join('');
  $('matches').querySelectorAll('[data-match-index]').forEach(button => {
    button.addEventListener('click', () => selectMatch(rows[Number(button.dataset.matchIndex)]));
  });
  $('matches').querySelectorAll('[data-favorite]').forEach(button=>button.addEventListener('click',()=>{
    const p=rows[Number(button.dataset.favorite)], saved=savedMatches(), key=matchKey(p);
    try{localStorage.setItem('football.favorites',JSON.stringify(favorites.has(key)?saved.filter(x=>matchKey(x)!==key):[...saved,p]));if(state.category==='guardados')loadMatches();else renderMatches()}catch{setMatchMessage('No se pudieron guardar los favoritos en este navegador.',true)}
  }));
}

async function selectMatch(match) {
  document.querySelectorAll('.analyze-btn').forEach(b => b.disabled = true);
  setMatchMessage('Iniciando análisis…');
  try {
    await api('/api/seleccionar', {method: 'POST', body: JSON.stringify(match)});
    state.active = true;
    state.browsing = false;
    showDashboard();
    await loadState(true);
  } catch (error) {
    setMatchMessage(error.message, true);
    document.querySelectorAll('.analyze-btn').forEach(b => b.disabled = false);
  }
}

async function stopAnalysis() {
  $('stopAnalysis').disabled = true;
  try {
    await api('/api/detener', {method: 'POST', body: '{}'});
    state.active = false;
    state.revision = -1;
    showSelector();
    await loadMatches();
  } catch (error) {
    showAlert(error.message);
  } finally {
    $('stopAnalysis').disabled = false;
  }
}

function showDashboard() {
  const wasHidden=$('dashboardView').classList.contains('hidden');
  $('selectorView').classList.add('hidden');
  $('dashboardView').classList.remove('hidden');
  if(wasHidden)window.scrollTo({top: 0, behavior: 'smooth'});
}

function showSelector() {
  state.browsing = true;
  $('dashboardView').classList.add('hidden');
  $('selectorView').classList.remove('hidden');
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function showAlert(text) {
  const alert = $('analysisAlert');
  alert.textContent = text || '';
  alert.classList.toggle('hidden', !text);
}

function probabilityRow(label, value, type = '') {
  const v = Math.max(0, Math.min(100, finite(value)));
  return `<div class="prob-row"><label title="${esc(label)}">${esc(label)}</label><div class="track"><div class="fill ${type}" style="width:${v}%"></div></div><strong>${v.toFixed(1)}%</strong></div>`;
}

function renderMetrics(d) {
  const m = d.metricas || {}, eq = d.equipos || {};
  const metrics = [
    ['Presión ' + (eq.local || 'Local'), m.riesgo_gol_local==null?'—':`${finite(m.riesgo_gol_local).toFixed(0)}/100`, 'home'],
    ['Presión ' + (eq.visitante || 'Visitante'), m.riesgo_gol_visitante==null?'—':`${finite(m.riesgo_gol_visitante).toFixed(0)}/100`, 'away'],
    ['Posesión local', pct(m.posesion_local), 'home'],
    ['Momentum local', pct(m.animo_local), 'home'],
  ];
  $('metricStrip').innerHTML = metrics.map(x => `<div class="metric ${x[2]}"><small>${esc(x[0])}</small><strong>${esc(x[1])}</strong></div>`).join('');
}

function renderPrediction(d) {
  const p = d.prediccion || {}, eq = d.equipos || {};
  const exists = Object.keys(p).length > 0;
  $('outcomeBars').innerHTML = exists ? [
    probabilityRow(eq.local || 'Local', p.prob_1x2_local),
    probabilityRow('Empate', p.prob_1x2_empate, 'draw'),
    probabilityRow(eq.visitante || 'Visitante', p.prob_1x2_visitante, 'away'),
  ].join('') : '<div class="timeline-empty">Esperando datos suficientes para calcular el pronóstico…</div>';
  $('markets').innerHTML = exists ? [
    probabilityRow('Más de 1.5', p.prob_over_1_5),
    probabilityRow('Más de 2.5', p.prob_over_2_5),
    probabilityRow('Más de 3.5', p.prob_over_3_5),
    probabilityRow('Ambos marcan', p.prob_btts, 'draw'),
  ].join('') : '<div class="timeline-empty">Sin cálculo disponible.</div>';
  $('modalScore').textContent = p.marcador_mas_probable || '—';
  $('goalExpectancy').textContent = exists ? `Goles totales estimados · ${finite(p.goles_esperados_local).toFixed(2)} local · ${finite(p.goles_esperados_visitante).toFixed(2)} visitante` : 'Esperando predicción';
  $('methodChip').textContent = p.metodo || 'Experimental';
  const warnings = Array.isArray(p.advertencias) ? p.advertencias.join(' · ') : '';
  $('methodologyText').textContent = warnings || 'Las probabilidades son condicionales a las tasas del modelo. La presión es un índice descriptivo, no una probabilidad de gol.';
  $('scoreAlternatives').innerHTML=(p.top_marcadores||[]).slice(0,3).map(x=>`<span><b>${esc(x.marcador)}</b> ${pct(x.prob)}</span>`).join('');
  $('nextGoal').innerHTML=exists?[probabilityRow(eq.local||'Local',p.prob_proximo_gol_local),probabilityRow(eq.visitante||'Visitante',p.prob_proximo_gol_visitante,'away'),probabilityRow('Sin más goles',p.prob_sin_mas_goles,'draw')].join(''):'<p class="muted">Esperando pronóstico vigente.</p>';
  $('tenMinute').innerHTML=p.prob_gol_10_min!=null?`<strong>${pct(p.prob_gol_10_min)}</strong><span>de al menos un gol en los próximos ${finite(p.ventana_gol_minutos)} min</span>`:'';
}

function prepareCanvas(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, rect.width), height = Math.max(160, rect.height);
  canvas.width = width * dpr; canvas.height = height * dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  return {ctx, width, height};
}

function drawLineChart(canvas, datasets, maxY = 100, minutes = []) {
  const {ctx, width, height} = prepareCanvas(canvas), pad = {l:34,r:14,t:14,b:28};
  ctx.clearRect(0,0,width,height); ctx.strokeStyle = '#253a2e'; ctx.fillStyle = '#91a89a'; ctx.font = '10px system-ui'; ctx.lineWidth = 1;
  for (let i=0;i<=4;i++){const y=pad.t+(height-pad.t-pad.b)*i/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(width-pad.r,y);ctx.stroke();ctx.fillText(String(Math.round(maxY*(1-i/4))),3,y+3)}
  const n = Math.max(1, ...datasets.map(d => d.values.length));
  const first=finite(minutes[0]),last=finite(minutes.at(-1));
  datasets.forEach(data => {ctx.beginPath();ctx.strokeStyle=data.color;ctx.lineWidth=2.2;let started=false;data.values.forEach((raw,i)=>{const v=Number(raw);if(raw==null||!Number.isFinite(v)){started=false;return}const fraction=last>first?(finite(minutes[i])-first)/(last-first):(n===1?.5:i/(n-1));const x=pad.l+(width-pad.l-pad.r)*fraction;const y=pad.t+(height-pad.t-pad.b)*(1-Math.max(0,Math.min(maxY,v))/maxY);started?ctx.lineTo(x,y):ctx.moveTo(x,y);started=true;if(n===1){ctx.arc(x,y,3,0,Math.PI*2)}});ctx.stroke()});
  ctx.fillStyle='#91a89a';ctx.fillText(minutes.length?`${first.toFixed(0)}′`:'Sin observaciones aún',pad.l,height-6);if(minutes.length>1)ctx.fillText(`${last.toFixed(0)}′`,width-38,height-6);
}

function drawBars(canvas, values) {
  const {ctx,width,height}=prepareCanvas(canvas), pad={l:24,r:12,t:12,b:28};
  ctx.clearRect(0,0,width,height);const max=Math.max(1,...values.map(v=>v.value));const gap=7;const w=(width-pad.l-pad.r-gap*(values.length-1))/Math.max(values.length,1);
  values.forEach((v,i)=>{const h=(height-pad.t-pad.b)*v.value/max;const x=pad.l+i*(w+gap),y=height-pad.b-h;ctx.fillStyle=v.color||'#75e29a';ctx.beginPath();ctx.roundRect(x,y,w,h,5);ctx.fill();ctx.fillStyle='#91a89a';ctx.font='10px system-ui';ctx.textAlign='center';ctx.fillText(v.label,x+w/2,height-9)});ctx.textAlign='left';
}

function renderCharts(d) {
  const s=d.series||{}, p=d.prediccion||{};
  drawLineChart($('trendChart'), [
    {values:s.riesgo_local||[],color:'#75e29a'},
    {values:s.riesgo_visitante||[],color:'#ff726f'},
    {values:s.animo_local||[],color:'#f3c969'},
  ],100,s.minutos||[]);
  const history=d.historial_mc||[];
  drawLineChart($('probabilityChart'),[{values:history.map(x=>x.prob_1x2_local),color:'#75e29a'},{values:history.map(x=>x.prob_1x2_empate),color:'#70b7ff'},{values:history.map(x=>x.prob_1x2_visitante),color:'#ff726f'}],100,history.map(x=>x.minuto));
  const probs=p.prob_goles_totales||{};
  let values=Object.entries(probs).filter(([k])=>Number(k)<=8).map(([label,value])=>({label,value:finite(value),color:'#70b7ff'}));
  if(!values.length){const hist=p.hist_goles_totales||{},n=Math.max(1,finite(p.n_iteraciones,250));values=Object.entries(hist).filter(([k])=>Number(k)<=8).map(([label,value])=>({label,value:100*finite(value)/n,color:'#70b7ff'}))}
  drawBars($('goalsChart'), values);
}

function renderHeatmap(d) {
  const p=d.prediccion||{}, matrix=p.matriz_marcadores||{}, rows=matrix.probabilidades||[], max=matrix.max_goles??6, current=d.marcador||{};
  if(!rows.length){$('heatmap').innerHTML='<div class="timeline-empty">La matriz aparecerá con la primera predicción.</div>';return}
  const highest=Math.max(1,...rows.flat().map(finite));
  $('matrixLimit').textContent=`Última celda = ${max}+`;
  let html='<table class="heatmap"><thead><tr><th>L \\ V</th>';
  for(let v=0;v<=max;v++)html+=`<th>${v===max?`${v}+`:v}</th>`;html+='</tr></thead><tbody>';
  for(let l=0;l<=max;l++){html+=`<tr><th>${l===max?`${l}+`:l}</th>`;for(let v=0;v<=max;v++){const value=finite(rows[l]?.[v]);const alpha=.12+.78*value/highest;const currentClass=current.local===l&&current.visitante===v?' current':'';html+=`<td class="${currentClass}" style="background:rgba(40,173,98,${alpha})">${value?value.toFixed(1)+'%':'·'}</td>`}html+='</tr>'}html+='</tbody></table>';$('heatmap').innerHTML=html;
}

function renderTimeline(d) {
  const events=(d.cronologia||[]).slice(-8).reverse();
  if(!events.length){$('timeline').innerHTML='<div class="timeline-empty">Sin eventos recientes.</div>';return}
  const icons={gol:'⚽',amarilla:'🟨',roja:'🟥',sustitucion:'↔️'};
  $('timeline').innerHTML=events.map(e=>`<div class="timeline-item"><time>${esc(e.minuto||'—')}'</time><span>${icons[e.tipo]||'•'}</span><div>${esc(e.texto||e.jugador||e.tipo||'Evento')}</div></div>`).join('');
}

function renderContext(d) {
  const q=d.calidad||{},p=d.prediccion||{},eq=d.equipos||{},stats=d.estadisticas||{};
  const age=q.edad_segundos;
  $('freshness').textContent=age==null?'Esperando datos':`${q.vigente?'● Actualizado':'◷ Datos retrasados'} · hace ${Math.floor(age)} s`;
  $('freshness').className=q.vigente?'fresh':'stale';
  $('coverage').innerHTML=`<b>${finite(q.cobertura)}<small>%</small></b><span>Cobertura de datos</span>`;
  const labels={tiros:'Remates',tiros_puerta:'A puerta',posesion:'Posesión',xg:'xG del proveedor',saques_esquina:'Córners',tarjetas_rojas:'Tarjetas rojas'};
  $('teamStats').innerHTML=Object.entries(labels).map(([key,label])=>{
    const l=stats[key]?.local,v=stats[key]?.visitante,known=l!=null&&v!=null;
    const total=finite(l)+finite(v),share=known?(total?100*finite(l)/total:50):50;
    const fmt=n=>n==null?'—':key==='xg'?finite(n).toFixed(2):key==='posesion'?pct(n):String(n);
    return `<div class="stat-row"><div><b>${fmt(l)}</b><span>${label}</span><b>${fmt(v)}</b></div><div class="duel-bar ${known?'':'unknown'}"><i style="width:${share}%"></i></div></div>`;
  }).join('');
  const sources={xg_proveedor:'xG observado',tiros_puerta:'Remates a puerta',tiros_totales:'Remates totales',prior_sin_estadisticas:'Promedio general'};
  $('evidence').innerHTML=`<div><span>Fuente</span><b>${esc(q.fuente||d.modo||'—')}</b></div><div><span>Local · señal usada</span><b>${esc(sources[p.evidencia?.local]||'Sin señal en vivo')}</b></div><div><span>Visitante · señal usada</span><b>${esc(sources[p.evidencia?.visitante]||'Sin señal en vivo')}</b></div><div><span>Punto de partida</span><b>${esc(p.fuente_prior||p.fuente||'Promedio general')}</b></div><p>La cobertura indica cuántas estadísticas están disponibles. No mide la probabilidad de acertar.</p>`;
  const valid=p.prob_1x2_local!=null;
  const ranked=[{name:eq.local||'Local',value:p.prob_1x2_local},{name:'El empate',value:p.prob_1x2_empate},{name:eq.visitante||'Visitante',value:p.prob_1x2_visitante}].sort((a,b)=>finite(b.value)-finite(a.value));
  const messages={datos_retrasados:'El proveedor dejó de actualizar: pronóstico suspendido hasta recibir datos recientes.',recalculando:'El partido cambió. Recalculando probabilidades con el nuevo marcador y minuto.'};
  $('liveInsight').textContent=messages[d.estado_prediccion]||(valid?`${ranked[0].name} es el escenario más probable (${pct(ranked[0].value)}). ${p.tiempo_restante!=null?`Horizonte del cálculo: ${finite(p.tiempo_restante).toFixed(0)} minutos restantes.`:'Pronóstico previo al inicio del partido.'}`:(d.minuto>=90?'El descuento no está confirmado. Esperando un horizonte válido o el resultado final.':'Esperando marcador, reloj y datos recientes para emitir una lectura.'));
}

function updateDashboard(d) {
  if(d.timestamp){
    const age=Math.max(0,(Date.now()-Date.parse(d.timestamp))/1000);
    if(Number.isFinite(age)){
      d={...d,calidad:{...d.calidad,edad_segundos:age,vigente:age<=60}};
      if(age>60&&d.minuto!=null)d={...d,prediccion:{},estado_prediccion:'datos_retrasados'};
    }
  }
  state.data=d;
  const eq=d.equipos||{}, control=d.control||{}, phase=control.fase||'en_vivo';
  state.active=phase!=='inicio';
  if(state.active&&!state.browsing)showDashboard();
  $('homeName').textContent=eq.local||'Local'; $('awayName').textContent=eq.visitante||'Visitante';
  const mark=d.marcador||{};$('score').innerHTML=`${mark.local==null?'—':finite(mark.local)} <span>:</span> ${mark.visitante==null?'—':finite(mark.visitante)}`;
  $('matchClock').textContent=d.minuto!=null?`${Math.round(finite(d.minuto))}' · ${d.status||''}`:(d.status||control.mensaje||'Prepartido');
  $('phaseBadge').textContent=({en_vivo:'En vivo',demo:'Demostración',cargando:'Conectando',prepartido:'Prepartido',prepartido_listo:'Prepartido',error:'Sin conexión'})[phase]||phase;
  $('competitionName').textContent=control.partido?.liga||'CENTRO DE ANÁLISIS';
  showAlert(control.error ? `${control.mensaje}: ${control.error}` : '');
  renderMetrics(d);renderPrediction(d);renderContext(d);renderHeatmap(d);renderTimeline(d);
  if(!$('dashboardView').classList.contains('hidden'))renderCharts(d);
}

async function loadState(force=false) {
  try {
    const d=await api('/api/data');const rev=finite(d.servidor?.revision,-1);
    setConnection('online', d.control?.mensaje||'Servidor activo');
    state.revision=rev;updateDashboard(d);
  } catch(error){setConnection('offline','Sin conexión');if(state.data)updateDashboard(state.data);showAlert('Conexión interrumpida. Intentando reconectar…')}
}

document.querySelectorAll('.tab').forEach(tab=>{tab.setAttribute('aria-selected',tab.classList.contains('active'));tab.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(t=>{t.classList.remove('active');t.setAttribute('aria-selected','false')});tab.classList.add('active');tab.setAttribute('aria-selected','true');state.category=tab.dataset.category;state.query='';state.league='';$('matchSearch').value='';loadMatches()})});
$('matchSearch').addEventListener('input',e=>{state.query=e.target.value;renderMatches()});
$('refreshMatches').addEventListener('click',loadMatches);
$('stopAnalysis').addEventListener('click',stopAnalysis);
$('backToMatches').addEventListener('click',()=>{showSelector();loadMatches()});
window.addEventListener('resize',()=>{if(state.active)loadState(true)});
document.querySelector('.brand').addEventListener('click',e=>{e.preventDefault();showSelector();loadMatches()});
$('leagueFilter').addEventListener('change',e=>{state.league=e.target.value;renderMatches()});
$('exportAnalysis').addEventListener('click',()=>{if(!state.data)return;const blob=new Blob([JSON.stringify({exportado_en:new Date().toISOString(),...state.data},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='football-analysis.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});

const stream=new EventSource('/api/stream');
stream.onmessage=e=>{try{const d=JSON.parse(e.data);state.revision=finite(d.servidor?.revision,state.revision);setConnection('online',d.control?.mensaje||'Datos actualizados');updateDashboard(d)}catch{}};
stream.onerror=()=>setConnection('offline','Reconectando…');
loadState(true).then(()=>{if(!state.active)loadMatches()});
setInterval(()=>loadState(false),3000);
