'use strict';
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = s => String(s).replaceAll('_',' ');
const external = u => {try {const a=new URL(u); return ['http:','https:'].includes(a.protocol)?a.href:'#';}catch{return '#';}};
const link = (u,t) => `<a href="${esc(external(u))}" target="_blank" rel="noopener noreferrer">${esc(t||u)} ↗</a>`;
const formatDate = d => d ? new Date(d).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'}):'Not reported';
const formatAmount = value => {const [whole,fraction] = String(value).split('.');return whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(fraction!==undefined?'.'+fraction:'');};
const PAGE_SIZE=100;
let companies=[], current=null, activeTab='overview', selected=null, compare=[], visibleCompanies=PAGE_SIZE;
let comparisonProfiles=new Map(), comparisonRevision=0, questionRevision=0;
const claim = field => current?.claims.find(c=>c.field===field);
const fields = families => current.claims.filter(c=>families.includes(c.family));
// Data layer: the run's static report (window.FJ_INDEX + lazily loaded chunks, works from file://)
// or the local server API. Both return the same envelope shapes.
const STATIC = typeof window.FJ_INDEX !== 'undefined';
function loadChunk(n){return new Promise((resolve,reject)=>{if(window.FJ_CHUNKS&&window.FJ_CHUNKS[n])return resolve();const s=document.createElement('script');s.src=`data/chunk-${String(n).padStart(4,'0')}.js`;s.onload=()=>resolve();s.onerror=()=>reject(new Error('Could not load data chunk'));document.head.appendChild(s);});}
async function getCompany(org){if(!STATIC)return fetch(`/api/company/${encodeURIComponent(org)}`).then(r=>r.json());const row=window.FJ_INDEX.find(r=>r.organisation_number===org);if(!row)throw new Error('Unknown company');await loadChunk(row.chunk);return window.FJ_CHUNKS[row.chunk][org];}
async function getCompanies(){return STATIC?{companies:window.FJ_INDEX,stats:window.FJ_STATS}:fetch('/api/companies').then(r=>r.json());}
function localAnswer(p,q){
  const config=window.FJ_ANSWER_CONFIG,query=String(q||''),catalog=p.question_answers||{};
  const unsupported=config.unsupported.filter(([pattern])=>new RegExp(pattern,'i').test(query)).map(([,reason])=>reason);
  const normalized=t=>t.toLowerCase().replace(/[?. ]+$/,'').trim();
  const exact=[...config.questions,config.online,config.overview].find(t=>normalized(t)===normalized(query));
  let keys=unsupported.length?[]:exact?[exact]:config.routes.filter(([,pattern])=>new RegExp(pattern,'i').test(query)).map(([key])=>key);
  if(keys.includes(config.questions[10]))keys=keys.filter(key=>key!==config.questions[3]);
  keys=[...new Set(keys)];
  const ordered=[...keys.filter(key=>config.questions.includes(key)),...([config.online,config.overview].filter(key=>keys.includes(key)))];
  const picked=ordered.map(key=>catalog[key]).filter(Boolean);
  if(!picked.length){const message=unsupported.join('; ')||'This question is not supported by the captured company facts. Ask about its business, leadership, locations, filed finances, hiring, website, employees, changes, activity or contact details.';return {answer:message,answerable:false,abstained:true,sentences:[{text:message,claim_ids:[],stale:false}],claim_ids:[],evidence_ids:[],claims:[],unsupported_or_uncertain:[message],changes:[]};}
  const ids=[...new Set(picked.flatMap(a=>a.claim_ids))].sort(),claims=p.claims.filter(c=>ids.includes(c.id)&&c.availability==='available'&&!c.stale);
  const sentences=picked.flatMap(a=>a.sentences),availability={};claims.forEach(c=>{availability[c.family]=p.availability[c.family]||{};});
  return {answer:sentences.map(s=>s.text).join(' '),answerable:picked.some(a=>a.answerable),abstained:!picked.some(a=>a.answerable),sentences,claim_ids:ids,evidence_ids:[...new Set(picked.flatMap(a=>a.evidence_ids))].sort(),claims,availability,unsupported_or_uncertain:[...new Set(picked.flatMap(a=>a.unsupported_or_uncertain))],changes:keys.includes(config.questions[7])?p.changes:[]};
}
async function askApi(org,q){return STATIC?localAnswer(current,q):fetch(`/api/ask?org=${encodeURIComponent(org)}&q=${encodeURIComponent(q)}`).then(r=>r.json());}
const latestClaim=(p,field,scope='legal_entity')=>p.claims.filter(c=>c.field===field&&(c.scope===scope||!c.reporting_period)).sort((a,b)=>String(b.reporting_period?.tilDato||'').localeCompare(String(a.reporting_period?.tilDato||'')))[0];
function valueHTML(v){
  if(v===null||v===undefined) return '<span class="subtle">Not reported</span>';
  if(typeof v==='boolean') return v?'Yes':'No';
  if(Array.isArray(v)) return v.map(valueHTML).join('<br>');
  if(typeof v==='object') {
    if('amount' in v) return `<span class="money">${esc(formatAmount(v.amount))} <small>${esc(v.currency)}</small></span><span class="subtle">${esc(v.account_type)} · Amount in currency units</span>`;
    return `<div class="data-list">${Object.entries(v).filter(([k])=>!['record_id','independent_sentiment'].includes(k)).map(([k,x])=>`<div class="data-pair"><span>${esc(label(k))}</span><span>${k==='url'?link(x,'Open source'):valueHTML(x)}</span></div>`).join('')}</div>`;
  }
  return esc(v);
}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,2400);}
function renderList(){
  const q=$('#search').value.toLowerCase(), filter=$('#filter').value, sort=$('#sort').value;
  let rows=companies.filter(c=>(`${c.name} ${c.organisation_number} ${c.city} ${c.industry}`).toLowerCase().includes(q));
  if(filter==='verified') rows=rows.filter(c=>c.website_verified);
  if(filter==='changed') rows=rows.filter(c=>c.changes);
  rows.sort(sort==='name'?(a,b)=>a.name.localeCompare(b.name):sort==='employees'?(a,b)=>(b.employees??-1)-(a.employees??-1):(a,b)=>b.claim_count-a.claim_count);
  $('#result-count').textContent=rows.length;
  $('#company-list').innerHTML=rows.length?rows.slice(0,visibleCompanies).map(c=>`<button class="company-row ${selected===c.organisation_number?'active':''}" data-company="${esc(c.organisation_number)}"><div class="row-top"><span class="initial">${esc(c.name.slice(0,1))}</span><div><div class="row-title">${esc(c.name)}</div><div class="row-meta">${esc(c.organisation_number)} · ${esc(c.city)}</div></div></div><div class="row-footer">${c.website_verified?'<span class="verified-tag">✓ Verified site</span>':''}${c.hiring?'<span class="verified-tag">Hiring</span>':''}<span>${c.claim_count} sourced claims</span></div></button>`).join(''):'<div class="empty">No companies match this search.</div>';
  if(!$('#list-pagination')) $('#company-list').insertAdjacentHTML('afterend','<div class="list-pagination" id="list-pagination"></div>');
  const shown=Math.min(visibleCompanies,rows.length), remaining=rows.length-shown;
  $('#list-pagination').innerHTML=`<span aria-live="polite">Showing ${shown.toLocaleString()} of ${rows.length.toLocaleString()}</span>${remaining?`<button class="button" id="load-more" aria-controls="company-list">Load ${Math.min(PAGE_SIZE,remaining)} more ↓</button>`:''}`;
}
function reveal(element){element.scrollIntoView({block:'start'});element.focus({preventScroll:true});}
function browseCompanies(){const input=$('#search');input.scrollIntoView({block:'center'});input.focus({preventScroll:true});input.select();}
function updateLocation(){try{history.replaceState(null,'',`#${selected?`org=${encodeURIComponent(selected)}`:''}${compare.length?`${selected?'&':''}compare=${compare.join(',')}`:''}`);}catch{}}
async function selectCompany(org, navigate=true){
  selected=org; renderList();
  $('#profile').setAttribute('aria-busy','true');
  const data=await getCompany(org);
  if(selected!==org)return;
  current=data;activeTab='overview';renderProfile();
  $('#profile').removeAttribute('aria-busy');
  updateLocation();
  if(navigate)reveal($('#profile'));
}
function renderProfile(){
  const p=current, verified=p.availability.website.state==='available', identity=p.legal_identity||{};
  const hasIdentity=typeof identity.name==='string'&&Boolean(identity.name.trim()), staleIdentity=Boolean(identity.stale||claim('legal_name')?.stale);
  const badge=!hasIdentity?'Identity unavailable':staleIdentity?'Registry identity · stale':verified?'✓ Exact website match':'Registry identity';
  const address=claim('registered_address')?.value, industry=claim('industry')?.value;
  $('#profile').innerHTML=`<div class="profile-header"><div class="profile-kicker"><span class="eyebrow">COMPANY DOSSIER / ${esc(p.organisation_number)}</span><span class="pill ${verified&&hasIdentity&&!staleIdentity?'':'muted'}">${badge}</span></div><h2 class="company-name">${esc(hasIdentity?identity.name:p.organisation_number)}</h2><div class="identity-line">${esc(identity.legal_form||'')} · ${esc(address?.poststed||'Location not reported')} · Norway<br>${esc(industry?.beskrivelse||'Industry not reported')}</div><div class="profile-actions"><button class="button" id="back-companies">Browse companies</button><button class="button" data-tab="ask">Ask a question</button>${verified?`<a class="button primary" href="${esc(external(claim('official_website')?.value))}" target="_blank" rel="noopener">Visit company website ↗</a>`:''}<button class="button" id="compare-button">＋ Compare company</button><button class="button" id="view-compare" ${compare.length?'':'hidden'}>View comparison (${compare.length})</button><button class="button" id="download-profile">Export profile ↓</button></div></div><nav class="tabs" aria-label="Profile sections">${['overview','financials','people','activity','evidence','changes','ask'].map(t=>`<button class="tab ${activeTab===t?'active':''}" data-tab="${t}">${t[0].toUpperCase()+t.slice(1)}</button>`).join('')}</nav><div class="tab-content" id="tab-content"></div>`;
  renderTab();
}
function citation(c,org=''){return c?`<button class="cite" data-claim="${esc(c.id)}" ${org?`data-evidence-org="${esc(org)}"`:''} aria-label="Inspect supporting evidence for ${esc(label(c.field))}">↗</button>`:'';}
function claimCard(c){return `<div class="claim-card"><div class="claim-head"><h4>${esc(label(c.field))}</h4><span class="state ${c.stale?'':'available'}">${c.stale?'Last known':'Sourced'}</span></div><div class="claim-value">${valueHTML(c.value)}</div><div class="claim-meta"><span>${esc(label(c.scope))}</span>${c.reporting_period?`<span>${esc(c.reporting_period.fraDato)} → ${esc(c.reporting_period.tilDato)}</span>`:''}${c.effective_at?`<span>Effective ${esc(c.effective_at)}</span>`:''}<button data-claim="${c.id}">Inspect ${c.evidence_ids.length} source${c.evidence_ids.length===1?'':'s'} ↗</button></div></div>`;}
function emptyFamily(f){const s=current.availability[f];return `<div class="notice"><b>${esc(label(s.state))}.</b> ${esc(s.reason)}</div>`;}
function renderTab(){
  const p=current, out=$('#tab-content');
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===activeTab));
  if(activeTab==='overview'){
    const factFields=[['employees','Registered employees'],['founded_on','Founded'],['registered_address','Registered address'],['latest_filing_year','Latest filed year']];
    const s=p.summary||{}, cite=ids=>(ids||[]).slice(0,3).map(id=>citation(p.claims.find(c=>c.id===id))).join('');
    const answered=(p.answers||[]).map(a=>`<div class="summary-section"><h4>${esc(a.question)}</h4><p>${esc(a.answer)}${cite(a.claim_ids)}</p></div>`).join('');
    const sections=(s.sections||[]).map(sec=>`<div class="summary-section"><h4>${esc(sec.title)}</h4>${sec.sentences.map(x=>`<p>${esc(x.text)}${cite(x.claim_ids)}${x.stale?' <span class="state">Last known</span>':''}</p>`).join('')}</div>`).join('');
    const legacy=(s.sentences||[]).slice(0,6).map(x=>`<p>${esc(x.text)}${cite(x.claim_ids)}</p>`).join('');
    const unknowns=(s.unknowns||[]).filter(u=>u.text).map(u=>`<li>${esc(u.text)}</li>`).join('');
    out.innerHTML=`<div class="block-title"><h3>The company, in context</h3><span class="subtle">Every sentence cites its sources ↗</span></div><div class="summary">${s.brief&&s.brief.text?`<p class="brief">${esc(s.brief.text)}${cite(s.brief.claim_ids)}</p>`:''}${sections||legacy||'<p>The captured sources do not establish a company summary.</p>'}</div>${answered?`<div class="block-title"><h3>Standard questions</h3><span class="subtle">Answered only from cited claims</span></div><div class="summary">${answered}</div>`:''}${unknowns?`<div class="block-title"><h3>What we could not establish, and why</h3><span class="subtle">Missing is never zero</span></div><ul class="unknowns">${unknowns}</ul>`:''}<div class="key-facts">${factFields.map(([field,title])=>{const c=claim(field);return `<div class="fact"><div class="fact-label">${title}</div><div class="fact-value">${field==='registered_address'&&c?esc([...(c.value.adresse||[]),c.value.postnummer,c.value.poststed].filter(Boolean).join(', ')):valueHTML(c?.value)}${citation(c)}</div></div>`;}).join('')}</div><div class="block-title"><h3>What we could establish</h3><span class="subtle">${p.claims.length} claims · ${p.evidence.length} evidence records</span></div><div class="health-grid">${Object.entries(p.availability).map(([family,s])=>`<div class="health-row" title="${esc(s.reason)}"><span>${esc(label(family))}</span><span class="state ${s.state}">${esc(label(s.state))}</span></div>`).join('')}</div>${askBox()}`;
  } else if(activeTab==='financials'){
    out.innerHTML='<div class="block-title"><h3>Filed financial results</h3><span class="subtle">Original currencies & reporting periods</span></div><div class="notice">Company and consolidated accounts are labelled separately. Missing values are not zero.</div>'+ (fields(['financials']).map(claimCard).join('')||emptyFamily('financials')) + '<div class="section-gap">'+fields(['filing_history']).map(claimCard).join('')+'</div>';
  } else if(activeTab==='people'){
    out.innerHTML='<div class="block-title"><h3>Registered leadership & workplaces</h3><span class="subtle">Official company-centric records</span></div>'+(fields(['leadership','locations','group']).map(claimCard).join('')||emptyFamily('leadership'));
  } else if(activeTab==='activity'){
    out.innerHTML='<div class="block-title"><h3>Public signals</h3><span class="subtle">Company-reported, dated where available</span></div>'+['hiring','activity','social_profiles','contact'].map(f=>`<h3 class="section-gap">${esc(label(f))}</h3>${fields([f]).map(claimCard).join('')||emptyFamily(f)}`).join('');
  } else if(activeTab==='evidence'){
    out.innerHTML=`<div class="block-title"><h3>Every claim has a trail</h3><span class="subtle">${(p.source_snapshot_count??p.source_snapshots.length)} saved response records</span></div><div class="notice">Select a claim to inspect its source URL, retrieval time, extraction selector, and content hash. Website claims require an exact legal identity match.</div>${p.claims.map(c=>`<div class="source-row"><div><b>${esc(label(c.field))}</b><div class="source-details">${esc(c.family)} · ${formatDate(c.last_observed_at)}${c.stale?' · stale':''}</div></div><button class="button" data-claim="${c.id}">Inspect ↗</button></div>`).join('')}`;
  } else if(activeTab==='ask'){
    out.innerHTML=askBox();
  } else {
    out.innerHTML=`<div class="block-title"><h3>Material changes</h3><span class="subtle">${p.changes.filter(c=>c.material!==false).length} material changes</span></div>${p.refresh.previous_run_id?`<p class="subtle">Compared with ${esc(p.refresh.previous_run_id)}</p>`:'<div class="notice">This is an initial observation. A refresh with a previous run is needed to establish changes.</div>'}${p.refresh.previous_run_id&&!p.changes.some(c=>c.material!==false)?'<div class="notice">No material change was established against the previous run.</div>':''}${p.changes.filter(c=>c.material!==false).map(changeCard).join('')}${p.refresh.stale_claim_ids.length?`<div class="notice">${p.refresh.stale_claim_ids.length} last-known claim(s) retained because fresh supporting evidence was unavailable. They are not counted as business changes.</div>`:''}`;
  }
}
function changeCard(c){return `<div class="claim-card"><h4>${esc(label(c.field))} · ${esc(label(c.type))}</h4><div class="source-details">Observed ${esc(c.observed_at||'Not reported')}</div><div class="data-pair"><span>Previous</span><span>${valueHTML(c.previous_value)}${(c.previous_evidence_ids||[]).length?`<button class="button" data-change="${esc(c.id)}" data-side="previous">Inspect previous evidence</button>`:''}</span></div><div class="data-pair"><span>Current</span><span>${valueHTML(c.current_value)}${(c.current_evidence_ids||[]).length?`<button class="button" data-change="${esc(c.id)}" data-side="current">Inspect current evidence</button>`:''}</span></div></div>`;}
function askBox(){return '<section class="ask-box"><h3>Ask the evidence</h3><p>Answers use this company’s saved claims and sources.</p><form class="ask-form" id="ask-form"><input id="question" aria-label="Question about this company" placeholder="What do we know about this company?" required><button class="button primary" type="submit">Ask ↗</button></form><div class="ask-chips">'+['Who leads it?','Is it hiring?','What changed?'].map(q=>`<button class="chip" data-question="${esc(q)}">${q}</button>`).join('')+'</div><div class="answer" id="answer" aria-live="polite"></div></section>';}
async function askQuestion(q){
  const revision=++questionRevision;$('#question').value=q;$('#answer').textContent='Checking the captured evidence...';const org=current.organisation_number;
  try{
    const r=await askApi(org,q);if(current.organisation_number!==org||revision!==questionRevision||!$('#answer'))return;
    const sentences=r.sentences?.length?r.sentences:[{text:r.answer,claim_ids:r.claim_ids||[]}];
    const text=sentences.map(s=>`<p class="answer-text">${esc(s.text)}${(s.claim_ids||[]).map(id=>citation(current.claims.find(c=>c.id===id))).join('')}</p>`).join('');
    const details=(r.claims||[]).length?`<details><summary>Inspect ${r.claims.length} supporting claim${r.claims.length===1?'':'s'}</summary>${r.claims.map(claimCard).join('')}</details>`:'';
    const changes=(r.changes||[]).filter(c=>c.material!==false).map(changeCard).join('');
    $('#answer').innerHTML=text+changes+details;
  }catch{if($('#answer')&&revision===questionRevision)$('#answer').textContent='Could not read the saved profile. Please try again.';}
}
function evidenceHTML(p,ids){return ids.map(id=>{
  const e=p.evidence.find(e=>e.id===id);
  if(!e)return '<div class="notice">The saved evidence record could not be found.</div>';
  return `<section class="evidence-block">${link(e.source_url)}<div class="source-details">Retrieved ${esc(e.retrieved_at)} · ${esc(label(e.source_class))}${e.quote_kind?` · ${esc(label(e.quote_kind))}`:''}</div><pre>${esc(e.claim_span)}</pre><div class="hash">CONTENT SHA-256<br>${esc(e.content_sha256)}<br><br>SELECTOR<br>${esc(JSON.stringify(e.selector))}<br><br>EXTRACTION<br>${esc(e.extraction_method)}</div>${e.identity_proof?`<div class="notice">Identity method: ${esc(e.identity_proof.method)}<br>${esc(e.identity_proof.reason||'Exact organisation number in official registry endpoint')}</div>`:''}</section>`;
}).join('');}
function openEvidence(html){$('#evidence-content').innerHTML=html;const dialog=$('#evidence-dialog');if(!dialog.open)dialog.showModal();dialog.scrollTop=0;}
function showEvidence(id,p=current){const c=p.claims.find(c=>c.id===id);if(!c)return;openEvidence(`<div class="source-details">${esc(p.legal_identity?.name||p.organisation_number)} · ${esc(p.organisation_number)}</div><h3>${esc(label(c.field))}</h3><div class="claim-value">${valueHTML(c.value)}</div><div class="claim-meta">${esc(label(c.scope))}${c.reporting_period?` · ${esc(c.reporting_period.fraDato)} to ${esc(c.reporting_period.tilDato)}`:''}${c.stale?' · Last known, not freshly verified':''}</div>${evidenceHTML(p,c.evidence_ids)}`);}
function showChangeEvidence(id,side){const c=current.changes.find(c=>c.id===id);if(!c||!['previous','current'].includes(side))return;openEvidence(`<h3>${esc(label(c.field))} · ${side} observation</h3><div class="claim-value">${valueHTML(c[side+'_value'])}</div>${evidenceHTML(current,c[side+'_evidence_ids']||[])}`);}
function currentClaims(p,field){return p.claims.filter(c=>c.field===field&&!c.stale&&c.availability==='available');}
function companyFinancials(p){
  const values=p.claims.filter(c=>c.family==='financials'&&c.scope==='legal_entity'&&!c.stale&&c.availability==='available'&&c.reporting_period);
  const groups=new Map();
  values.forEach(c=>{const key=JSON.stringify([c.reporting_period.fraDato||'',c.reporting_period.tilDato||'']);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(c);});
  const periods=[...groups.keys()].sort((a,b)=>JSON.parse(b)[1].localeCompare(JSON.parse(a)[1])||groups.get(b).length-groups.get(a).length||JSON.parse(a)[0].localeCompare(JSON.parse(b)[0]));
  return groups.get(periods[0])||[];
}
function comparisonCell(p,claims,value,financial=false){
  if(value===null||value===undefined||value==='')return '<span class="subtle">Not established from captured sources</span>';
  const metadata=financial&&claims[0]?`<div class="source-details">Company accounts · ${esc(claims[0].reporting_period.fraDato)} to ${esc(claims[0].reporting_period.tilDato)}</div>`:'';
  return `${typeof value==='string'&&/^https?:/.test(value)?link(value,value):esc(value)}${claims.map(c=>citation(c,p.organisation_number)).join('')}${metadata}`;
}
async function renderCompare(navigate=false){
  const revision=++comparisonRevision,panel=$('#compare-panel');
  panel.hidden=!compare.length;updateLocation();
  const view=$('#view-compare');if(view){view.hidden=!compare.length;view.textContent=`View comparison (${compare.length})`;}
  if(!compare.length){comparisonProfiles.clear();return;}
  const rows=await Promise.all(compare.map(org=>getCompany(org)));if(revision!==comparisonRevision)return;
  comparisonProfiles=new Map(rows.map(p=>[p.organisation_number,p]));
  const simple=(field,extract=v=>v)=>p=>{const c=currentClaims(p,field)[0];return comparisonCell(p,c?[c]:[],c?extract(c.value):null);};
  const money=field=>p=>{const c=companyFinancials(p).find(c=>c.field===field);return comparisonCell(p,c?[c]:[],c?`${formatAmount(c.value.amount)} ${c.value.currency}`:null,true);};
  const role=code=>p=>{const cs=currentClaims(p,'registered_role').filter(c=>c.value?.role_code===code);return comparisonCell(p,cs,cs.map(c=>c.value.name).join(', '));};
  const count=field=>p=>{const cs=currentClaims(p,field);return comparisonCell(p,cs,cs.length?cs.length:null);};
  const ratio=p=>{const cs=companyFinancials(p),e=cs.find(c=>c.field==='financial_equity'),a=cs.find(c=>c.field==='financial_assets');if(!e||!a||e.value.currency!==a.value.currency||Number(a.value.amount)<=0)return comparisonCell(p,[],null);const x=Number(e.value.amount)/Number(a.value.amount)*100;return comparisonCell(p,[e,a],isFinite(x)?x.toFixed(1)+'% (equity / assets, calculated)':null,true);};
  const facts=[['Industry',simple('industry',v=>v?.beskrivelse)],['Legal form',simple('legal_form')],['Registered employees',simple('employees')],['Revenue',money('financial_revenue')],['Operating result',money('financial_operating_profit')],['Annual result',money('financial_net_profit')],['Equity',money('financial_equity')],['Equity ratio',ratio],['Chief executive',role('DAGL')],['Board chair',role('LEDE')],['Verified website',simple('official_website')],['Registered workplaces',count('registered_workplace')],['Captured job ads',count('job_posting')]];
  panel.innerHTML=`<div class="comparison-head"><h2>Compare sourced facts</h2><div><button class="button" id="add-compare">Add another company</button> <button class="button" id="clear-compare">Clear comparison</button></div></div><p class="subtle">Up to three companies. Each financial value shows its reporting period; different periods may not be comparable. Missing values are not zero.</p><div class="table-scroll" tabindex="0" aria-label="Scrollable comparison table"><table><thead><tr><th scope="col">Company fact</th>${rows.map(p=>`<th scope="col">${esc(p.legal_identity?.name||p.organisation_number)}<div class="source-details">${esc(p.organisation_number)}</div><button class="button" data-company="${esc(p.organisation_number)}">Open profile</button> <button class="button" data-remove-compare="${esc(p.organisation_number)}" aria-label="Remove ${esc(p.legal_identity?.name||p.organisation_number)} from comparison">Remove</button></th>`).join('')}</tr></thead><tbody>${facts.map(([name,fn])=>`<tr><th scope="row">${esc(name)}</th>${rows.map(p=>`<td>${fn(p)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  if(navigate)reveal(panel);
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  if(b.id==='load-more'){const previous=visibleCompanies;visibleCompanies+=PAGE_SIZE;renderList();const next=$('#company-list').querySelectorAll('[data-company]')[previous];if(next){next.focus({preventScroll:true});next.scrollIntoView({block:'nearest'});}return;}
  if(b.dataset.company)selectCompany(b.dataset.company).catch(()=>{ $('#profile').removeAttribute('aria-busy');toast('Could not load company');});
  if(b.dataset.tab){activeTab=b.dataset.tab;renderTab();if(activeTab==='ask')$('#question').focus({preventScroll:true});}
  if(b.dataset.claim)showEvidence(b.dataset.claim,b.dataset.evidenceOrg?comparisonProfiles.get(b.dataset.evidenceOrg):current);
  if(b.dataset.change)showChangeEvidence(b.dataset.change,b.dataset.side);
  if(b.dataset.question)askQuestion(b.dataset.question);
  if(b.id==='close-evidence')$('#evidence-dialog').close();
  if(b.id==='back-companies'||b.id==='add-compare')browseCompanies();
  if(b.id==='view-compare')reveal($('#compare-panel'));
  if(b.id==='compare-button'){
    if(!compare.includes(selected)){if(compare.length===3)return toast('Remove a company before adding a fourth');compare.push(selected);}
    try{await renderCompare(true);}catch{toast('Could not load comparison');}
  }
  if(b.id==='clear-compare'){compare=[];renderCompare();reveal($('#profile'));}
  if(b.dataset.removeCompare){compare=compare.filter(org=>org!==b.dataset.removeCompare);await renderCompare(true);if(!compare.length)reveal($('#profile'));}
  if(b.id==='download-profile'){const u=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download=`fjordlens-${selected}.json`;a.click();URL.revokeObjectURL(u);}
});
document.addEventListener('submit',e=>{if(e.target.id==='ask-form'){e.preventDefault();askQuestion($('#question').value);}});
document.addEventListener('keydown',e=>{if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('#search').focus();}});
['search','filter','sort'].forEach(id=>$('#'+id).addEventListener(id==='search'?'input':'change',()=>{visibleCompanies=PAGE_SIZE;$('#company-list').scrollTop=0;renderList();}));
async function applyLocation(initial=false){
  const h=new URLSearchParams(location.hash.slice(1)),want=h.get('compare');
  compare=want?[...new Set(want.split(',').filter(o=>companies.some(r=>r.organisation_number===o)))].slice(0,3):[];
  const org=h.get('org')&&companies.some(r=>r.organisation_number===h.get('org'))?h.get('org'):(companies.length?companies[0].organisation_number:null);
  if(org)await selectCompany(org,Boolean(h.get('org'))&&!compare.length);
  await renderCompare(Boolean(compare.length));
  if(initial&&!h.toString()){try{history.replaceState(null,'',location.href.split('#')[0]);}catch{}}
  if(!initial&&!h.get('org')&&!compare.length)browseCompanies();
}
window.addEventListener('hashchange',()=>{if(companies.length)applyLocation().catch(()=>toast('Could not open the saved selection'));});
getCompanies().then(async ({companies:c,stats:s})=>{
  companies=c;$('#stat-profiles').textContent=s.profiles.toLocaleString();$('#stat-claims').textContent=s.claims.toLocaleString();$('#stat-sites').textContent=s.verified_sites.toLocaleString();$('#stat-date').textContent=formatDate(s.retrieved_at);renderList();
  await applyLocation(true);
}).catch(()=>{$('#company-list').innerHTML='<div class="empty">Could not load the collection. Open the report written by a completed run, or start the server with an envelopes file.</div>';});
