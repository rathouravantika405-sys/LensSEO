'use strict';
let report = null;
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const cleanURL = value => {try { const u=new URL(value); return ['http:','https:'].includes(u.protocol)?u.href:'#'; } catch {return '#'; }};
const pathOf = value => {try {let u=new URL(value); return u.hostname + (u.pathname==='/'?'':u.pathname) + u.search; } catch {return value}};
const status = (message, error=false) => {
  const n=$('notice');n.hidden=!message;n.classList.toggle('error',error);n.textContent=message;
};
const clamp = x => Math.min(100,Math.max(0,Number(x)||0));
const displayTab = key => {
  ['issues','pages','ai'].forEach(t=>{
    $(t==='issues'?'issuePanel':t==='pages'?'pagePanel':'aiPanel').hidden = key!==t;
    document.querySelector(`.tab[data-tab="${t}"]`).classList.toggle('active',key===t);
  });
};
function filteredFindings(){
  if(!report) return [];
  let query=$('searchIssues').value.toLowerCase().trim();
  return report.findings.filter(row=>
    ($('filterPriority').value==='all'||row.priority===$('filterPriority').value) &&
    ($('filterCategory').value==='all'||row.category.includes($('filterCategory').value)) &&
    (!query||[row.url,row.issue,row.category,row.priority,row.fix].some(x=>String(x??'').toLowerCase().includes(query)))
  );
}
function renderIssues(){
  const rows=filteredFindings();
  $('issuesBody').innerHTML=rows.map(x=>`<tr>
    <td title="${escapeHTML(x.url)}"><a class="report-url" href="${escapeHTML(cleanURL(x.url))}" target="_blank" rel="noopener noreferrer">${escapeHTML(pathOf(x.url))}</a></td>
    <td>${escapeHTML(x.issue)}${x.evidence?`<div style="font-size:10px;color:#9ca5b4;font-weight:400;margin-top:5px">${escapeHTML(x.evidence)}</div>`:''}</td>
    <td><span class="type-tag">${escapeHTML(x.category)}</span></td>
    <td><span class="priority-tag ${escapeHTML(x.priority.toLowerCase())}">${escapeHTML(x.priority)}</span></td>
    <td>${escapeHTML(x.fix)}</td></tr>`).join('') || `<tr><td colspan="5">No matching findings.</td></tr>`;
  $('visibleCount').textContent=`Showing ${rows.length} of ${report.findings.length} findings`;
}
function renderPages(){
  const pages=report.pages || [];
  $('pagesBody').innerHTML=pages.map(p=>`<tr><td>${escapeHTML(pathOf(p.url))}</td><td>${escapeHTML(p.status)}</td><td>${escapeHTML(p.title||'Not found')}</td><td>${escapeHTML(p.word_count)}</td><td>${escapeHTML(p.inlinks)}</td><td>${escapeHTML(p.response_ms)} ms</td></tr>`).join('') || `<tr><td colspan="6">No individual pages in the sample dataset.</td></tr>`;
}
function renderAI(){
  let ai=report.ai_suggestions;
  if(!ai){$('aiArea').innerHTML=`<div class="ai-note"><b>AI status:</b> ${escapeHTML(report.ai_status||'No AI requests made.')}<br><br>For a live AI-enhanced report, configure OPENAI_API_KEY on the server and select “Generate AI rewrite suggestions” before starting a scan. The basic audit does not require an API key.</div>`;return}
  $('aiArea').innerHTML=`<div class="ai-note"><b>AI review:</b> ${escapeHTML(ai.summary)}</div>
    ${ai.opportunities?.length?`<h3>Potential content opportunities</h3><ul>${ai.opportunities.map(x=>`<li>${escapeHTML(x)}</li>`).join('')}</ul>`:''}
    ${ai.rewrites?.length?`<h3>Suggested copy (review before publishing)</h3>${ai.rewrites.map(x=>`<div class="rewrite-card"><b>${escapeHTML(pathOf(x.url))}</b><p><b>Title:</b> ${escapeHTML(x.proposed_title)}</p><p><b>Meta:</b> ${escapeHTML(x.proposed_meta)}</p><p><b>Opening answer:</b> ${escapeHTML(x.suggested_opening_answer)}</p></div>`).join('')}`:''}
    <div class="ai-note">AI drafts are suggestions based on the provided page excerpts, not automatically verified facts. Check every statement before publishing.</div>`;
}
function showReport(data){
  report=data;
  $('results').hidden=false;
  $('demoBadge').hidden=!data.is_demo;
  $('auditedDomain').textContent=`${data.url} · ${data.is_demo?'Illustrative data':`${data.stats.pages} pages scanned · ${new Date(data.audited_at).toLocaleString()}`}`;
  for (const key of ['seo','aeo','geo']){
    const val=clamp(data.stats.scores[key.toUpperCase()]);
    $(key+'Score').textContent=val;
    $(key+'Bar').style.width=val+'%';
  }
  $('pageCount').textContent=data.stats.pages;
  $('issueCount').textContent=data.stats.issues;
  $('issuesSummary').textContent=`${data.stats.high} high · ${data.stats.medium} medium · ${data.stats.low} low`;
  $('scoreNote').textContent=data.stats.score_note;
  const warnings=[...(data.warnings||[]),...(data.failed_urls||[]).map(x=>`Skipped ${x.url}: ${x.error}`)];
  $('warningsPanel').hidden=warnings.length===0;
  $('warningsPanel').open=false;
  $('warningCount').textContent=warnings.length;
  $('warningsList').innerHTML=warnings.map(w=>`<li>${escapeHTML(w)}</li>`).join('');
  $('issueTabCount').textContent=data.findings.length;
  $('searchIssues').value='';$('filterPriority').value='all';$('filterCategory').value='all';
  renderIssues();renderPages();renderAI();displayTab('issues');
  status(data.is_demo?'This is fictional demo data, not a real website scan.':
    `Scan completed. ${data.stats.pages} pages audited${data.competitor_count?', '+data.competitor_count+' competitor pages compared':''}.${data.warnings?.length?' Some pages or resources were skipped; see JSON export.':''}`);
  $('results').scrollIntoView({behavior:'smooth',block:'start'});
}
async function runAudit(event){
  event.preventDefault();
  const button=$('runAudit');button.disabled=true;
  report=null; $('results').hidden=true;
  $('runLabel').textContent='Crawling and analyzing…';
  status('Scanning public pages. This can take a minute if the site is slow or restricts crawlers.');
  const competitor_urls=$('competitorUrls').value.split('\n').map(s=>s.trim()).filter(Boolean).slice(0,3);
  try{
    const res=await fetch('/api/audit',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:$('siteUrl').value,max_pages:Number($('maxPages').value),competitor_urls,use_ai:$('useAI').checked,section_only:$('sectionOnly').checked})});
    let body=await res.json();
    if(!res.ok) throw Error(typeof body.detail==='string'?body.detail:`The audit failed (HTTP ${res.status}).`);
    showReport(body);
  }catch(error){status(error.message||'The audit could not be completed.',true)}
  finally{button.disabled=false;$('runLabel').textContent='Run website audit'}
}
async function runDemo(){
  try{status('Loading illustrative demo…');const res=await fetch('/api/demo');if(!res.ok)throw Error('Demo could not load.');showReport(await res.json())}
  catch(err){status(err.message,true)}
}
function csvEscape(v){
  let value=String(v??'');
  // Stop CSV cells being interpreted as formulas by spreadsheet applications.
  if (/^\s*[=+\-@]/.test(value)) value="'"+value;
  return /[",\r\n]/.test(value)?'"'+value.replace(/"/g,'""')+'"':value;
}
function download(contents,filename,type){let blob=new Blob([contents],{type});let url=URL.createObjectURL(blob);let a=document.createElement('a');a.href=url;a.download=filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),3000)}
function downloadCSV(){
  if(!report)return;
  const cols=['URL','Issue','SEO/AEO/GEO','Priority','Recommended Fix'];
  const rows=report.report_rows || report.findings.map(r=>({'URL':r.url,'Issue':r.issue,'SEO/AEO/GEO':r.category,'Priority':r.priority,'Recommended Fix':r.fix}));
  const out=[cols,...rows.map(item=>cols.map(name=>item[name]??''))].map(row=>row.map(csvEscape).join(',')).join('\r\n');
  download('\ufeff'+out,'LensSEO_5_Column_Audit_Report.csv','text/csv;charset=utf-8');
}
function downloadJSON(){if(report)download(JSON.stringify(report,null,2),'lensseo_full_report.json','application/json')}
function openHelp(){let dialog=$('helpDialog');if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','')}
async function configurePublicDemo(){
  try {
    const res=await fetch('/api/health');if(!res.ok)return;
    const cfg=await res.json();if(!cfg.public_demo)return;
    $('maxPages').innerHTML='<option value="5">5 pages · quick</option><option value="8" selected>8 pages · demo max</option>';
    $('competitorToggle').disabled=true;
    $('competitorToggle').title='Competitor page fetching is disabled in this public demonstration.';
    $('useAI').disabled=true;
    $('useAI').checked=false;
    $('useAI').closest('label').querySelector('small').textContent='AI rewrites are disabled in the free public demo.';
    document.querySelector('.small-pill').textContent='PUBLIC DEMO';
    document.querySelector('.side-small').textContent='Public interview demo · v1.2';
    document.querySelector('.modal-note').textContent='Public demo scans up to 8 pages, restricted to PW websites and example.org. The sample report works without crawling. AI scores are readiness heuristics, not measured citation likelihood.';
  }catch(e){/* Local/offline mode stays available. */}
}
document.addEventListener('DOMContentLoaded',()=>{
  configurePublicDemo();
  $('auditForm').addEventListener('submit',runAudit);
  $('demoButton').addEventListener('click',runDemo);
  $('sampleSide').addEventListener('click',runDemo);
  $('newAudit').addEventListener('click',()=>{window.scrollTo({top:0,behavior:'smooth'});$('siteUrl').focus()});
  $('guideSide').addEventListener('click',openHelp);$('helpButton').addEventListener('click',openHelp);
  $('competitorToggle').addEventListener('click',()=>{$('competitorPanel').hidden=!$('competitorPanel').hidden;$('toggleArrow').textContent=$('competitorPanel').hidden?'↓':'↑'});
  document.querySelectorAll('.tab').forEach(el=>el.addEventListener('click',()=>displayTab(el.dataset.tab)));
  ['searchIssues','filterPriority','filterCategory'].forEach(id=>$(id).addEventListener('input',renderIssues));
  $('downloadCSV').addEventListener('click',downloadCSV);$('downloadJSON').addEventListener('click',downloadJSON);
});
