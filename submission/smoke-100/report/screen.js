'use strict';
// Cross-company screening in the browser. Mirrors fjordlens/research.py parse_screen_query: a closed
// grammar becomes an inspectable plan of explicit filters, or the agent abstains. Missing values never
// satisfy a numeric filter (missing is not zero).
const SCREEN_UNSUPPORTED = {
  'sentiment': 'sentiment is not qualified: no labelled Norwegian evaluation corpus has been run',
  'glassdoor': 'Glassdoor data is not available through a permitted connector',
  'linkedin': 'LinkedIn-derived data is not available through a permitted connector',
  'traffic': 'website traffic is not available through a qualified provider',
  'review': 'review and rating data is not available through a qualified provider',
  'buzz': 'social buzz is not available through a qualified provider',
  'popular': 'popularity is not measured by any qualified source',
  'fraud': 'fraud cannot be established from the collected public records',
  'without a website': 'a missing or unverified website does not prove a company has no website',
  'no website': 'a missing or unverified website does not prove a company has no website',
  'velocity': 'hiring velocity needs a time series this collection does not hold',
  'culture': 'workplace culture is not measured by any qualified source'
};
const SCREEN_OPS = {'more than': '>', 'over': '>', 'above': '>', 'greater than': '>', 'at least': '>=', 'minimum': '>=',
  'fewer than': '<', 'less than': '<', 'under': '<', 'below': '<', 'at most': '<=', 'maximum': '<='};
const SCREEN_FORMS = 'asa|as|enk|nuf|ans|da|sa|sti|brl|ba|ks|iks|fli|esek|sam|spa|kf|bbl';

function screenAmount(text, unit) {
  const value = Number(String(text).replace(/\s/g, '').replace(',', '.'));
  const u = String(unit || '').toLowerCase();
  return value * (['billion', 'bn', 'mrd'].includes(u) ? 1e9 : ['million', 'm', 'mill', 'mnok'].includes(u) ? 1e6 : 1);
}

function parseScreen(query) {
  const text = String(query || '').trim().split(/\s+/).join(' ');
  const lower = text.toLowerCase();
  const filters = [];
  const unsupported = [...new Set(Object.entries(SCREEN_UNSUPPORTED).filter(([t]) => lower.includes(t)).map(([, m]) => m))];
  const muni = lower.match(/\b(?:in|located in|municipality(?:\s+is|\s*=)?)\s+([a-zæøåéü .'-]+?)(?=\s+(?:with|and|having|that|where|top|sorted|by)\b|$)/);
  if (muni && !['norway', 'norge'].includes(muni[1].trim())) filters.push({field: 'municipality', operator: 'eq', value: muni[1].trim().toUpperCase()});
  const form = lower.match(new RegExp(`\\b(?:legal\\s+form|organisation\\s+form|organization\\s+form|form)\\s*(?:is|=)?\\s*(${SCREEN_FORMS})\\b`));
  if (form) filters.push({field: 'legal_form', operator: 'eq', value: form[1].toUpperCase()});
  const emp = lower.match(/\b(more than|over|above|greater than|at least|fewer than|less than|under|below|at most)\s+(\d+)\s+(?:registered\s+)?(?:employees?|ansatte)\b/)
    || lower.match(/\b(?:employees?|ansatte)\s*(>=|<=|>|<|=)\s*(\d+)\b/);
  if (emp) filters.push({field: 'employees', operator: SCREEN_OPS[emp[1]] || emp[1], value: Number(emp[2])});
  const rev = lower.match(/\b(?:revenue|turnover|omsetning)\s*(>=|<=|>|<|=|more than|over|above|greater than|at least|fewer than|less than|under|below|at most)\s*(?:nok\s*)?(\d[\d ,.]*)\s*(billion|bn|million|m|mill|mnok|mrd)?\b/)
    || lower.match(/\b(more than|over|above|greater than|at least|fewer than|less than|under|below|at most)\s*(?:nok\s*)?(\d[\d ,.]*)\s*(billion|bn|million|m|mill|mnok|mrd)?\s+(?:in\s+)?(?:revenue|turnover|omsetning)\b/);
  if (rev) filters.push({field: 'revenue', operator: SCREEN_OPS[rev[1]] || rev[1], value: screenAmount(rev[2].replace(/[ ,.]+$/, ''), rev[3])});
  if (/\b(unprofitable|loss[- ]making|negative annual result)\b/.test(lower)) filters.push({field: 'annual_result', operator: '<', value: 0});
  else if (/\b(profitable|positive annual result)\b/.test(lower)) filters.push({field: 'annual_result', operator: '>', value: 0});
  if (/\b(?:with|has|have|having)\s+(?:an?\s+)?(?:verified\s+|official\s+)?website\b/.test(lower)) filters.push({field: 'website', operator: 'present', value: true});
  if (/\b(?:with|has|have|having)\s+(?:filed\s+|annual\s+)?accounts\b/.test(lower)) filters.push({field: 'financials', operator: 'available', value: true});
  if (/\b(?:hiring|with (?:open )?(?:jobs|job ads|vacancies))\b/.test(lower)) filters.push({field: 'hiring', operator: 'present', value: true});
  const ind = text.match(/\bindustry(?:\s+contains|\s+is|\s*=)?\s+["']([^"']+)["']/i);
  if (ind) filters.push({field: 'industry', operator: 'contains', value: ind[1].toLowerCase()});
  const top = lower.match(/\btop\s+(\d+)\s+(?:companies\s+)?by\s+(revenue|employees)\b/);
  const sort = top ? {field: top[2], direction: 'desc', limit: Math.max(1, Math.min(Number(top[1]), 100))} : null;
  return {query: text, filters, sort, unsupported, executable: Boolean(filters.length || sort) && !unsupported.length};
}

function screenValue(row, field) {
  switch (field) {
    case 'municipality': return row.municipality ?? null;
    case 'legal_form': return row.legal_form ?? null;
    case 'employees': return row.employees ?? null;
    case 'revenue': return row.revenue ?? null;
    case 'annual_result': return row.annual_result ?? null;
    case 'website': return Boolean(row.website_verified);
    case 'financials': return row.revenue !== null && row.revenue !== undefined;
    case 'hiring': return Boolean(row.hiring);
    case 'industry': return `${row.industry_code || ''} ${row.industry || ''}`;
    default: return null;
  }
}

function screenMatch(actual, op, expected) {
  if (op === 'eq') return actual !== null && String(actual).toLowerCase() === String(expected).toLowerCase();
  if (op === 'present' || op === 'available') return Boolean(actual) === Boolean(expected);
  if (op === 'contains') return actual !== null && String(actual).toLowerCase().includes(String(expected).toLowerCase());
  if (actual === null || actual === undefined) return false;
  return {'>': actual > expected, '>=': actual >= expected, '<': actual < expected, '<=': actual <= expected, '=': actual === expected}[op];
}

function runScreen(query) {
  const plan = parseScreen(query);
  if (!plan.executable) return {plan, results: [], abstained: true, reason: plan.unsupported.join('; ') || 'No supported criterion was recognized. Try: companies in Oslo with more than 5 employees top 10 by revenue'};
  let results = companies.filter(row => plan.filters.every(f => screenMatch(screenValue(row, f.field), f.operator, f.value)));
  if (plan.sort) {
    const key = plan.sort.field;
    results = results.slice().sort((a, b) => ((a[key] ?? null) === null) - ((b[key] ?? null) === null) || (b[key] ?? 0) - (a[key] ?? 0) || a.organisation_number.localeCompare(b.organisation_number)).slice(0, plan.sort.limit);
  } else results = results.slice().sort((a, b) => a.organisation_number.localeCompare(b.organisation_number));
  return {plan, results, abstained: false};
}

const SCREEN_HISTORY_KEY = 'fjordlens-screen-history';
function screenHistory() { try { return JSON.parse(localStorage.getItem(SCREEN_HISTORY_KEY) || '[]'); } catch { return []; } }
function saveScreen(query) { try { localStorage.setItem(SCREEN_HISTORY_KEY, JSON.stringify([query, ...screenHistory().filter(q => q !== query)].slice(0, 8))); } catch { /* storage unavailable */ } }
const money = v => v === null || v === undefined ? 'Not filed' : `NOK ${Math.round(v).toLocaleString('en-GB').replace(/,/g, ' ')}`;
let lastScreen = null;

function renderScreen(result) {
  lastScreen = result;
  const panel = $('#screen-results');
  if (!result) { panel.hidden = true; panel.innerHTML = ''; return; }
  panel.hidden = false;
  const chips = result.plan.filters.map(f => `<span class="chip plan-chip">${esc(f.field)} ${esc(f.operator)} ${esc(f.value)}</span>`).join('') +
    (result.plan.sort ? `<span class="chip plan-chip">top ${esc(result.plan.sort.limit)} by ${esc(result.plan.sort.field)}</span>` : '');
  if (result.abstained) {
    panel.innerHTML = `<div class="notice"><b>Not answered.</b> ${esc(result.reason)}</div><button class="button" id="clear-screen">Clear</button>`;
    return;
  }
  panel.innerHTML = `<div class="screen-plan"><span class="subtle">Plan (inspectable):</span> ${chips || '<span class="subtle">no filters</span>'}</div>` +
    `<div class="screen-count">${result.results.length.toLocaleString()} matching ${result.results.length === 1 ? 'company' : 'companies'}</div>` +
    result.results.slice(0, 100).map(r => `<button class="company-row screen-row" data-company="${esc(r.organisation_number)}"><div class="row-title">${esc(r.name)}</div>` +
      `<div class="row-meta">${esc(r.organisation_number)} · ${esc(r.municipality || r.city || '')} · ${r.employees ?? 'employees not reported'} employees · ${esc(money(r.revenue))}</div></button>`).join('') +
    `<div class="screen-actions"><button class="button" id="export-screen">Export results ↓</button><button class="button" id="clear-screen">Clear</button></div>` +
    `<p class="subtle">Open a company to inspect the source behind each value.</p>`;
}

function setupScreen() {
  const host = document.querySelector('.directory .search');
  if (!host || $('#screen-form')) return;
  host.insertAdjacentHTML('afterend', `<form class="screen-form" id="screen-form"><label class="subtle" for="screen-query">Screen companies</label>` +
    `<div class="screen-input"><input id="screen-query" placeholder="e.g. companies in Oslo with more than 5 employees top 10 by revenue" aria-label="Screen companies with a closed query">` +
    `<button class="button" type="submit">Screen</button></div><div class="ask-chips" id="screen-history"></div></form><section id="screen-results" class="screen-results" hidden aria-live="polite"></section>`);
  const drawHistory = () => { $('#screen-history').innerHTML = screenHistory().map(q => `<button type="button" class="chip" data-screen="${esc(q)}">${esc(q)}</button>`).join(''); };
  drawHistory();
  $('#screen-form').addEventListener('submit', e => { e.preventDefault(); const q = $('#screen-query').value.trim(); if (!q) return; const r = runScreen(q); renderScreen(r); if (!r.abstained) { saveScreen(q); drawHistory(); } });
  document.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.screen) { $('#screen-query').value = b.dataset.screen; renderScreen(runScreen(b.dataset.screen)); }
    if (b.id === 'clear-screen') { renderScreen(null); $('#screen-query').value = ''; }
    if (b.id === 'export-screen' && lastScreen) {
      const blob = new Blob([JSON.stringify({plan: lastScreen.plan, results: lastScreen.results}, null, 2)], {type: 'application/json'});
      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'fjordlens-screen.json'; a.click(); URL.revokeObjectURL(a.href);
    }
  });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setupScreen); else setupScreen();
