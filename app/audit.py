"""Bounded, rules-based SEO/AEO/GEO audit with optional AI analysis.

Scores are readiness heuristics, *not* search rankings, AI citation probabilities,
or certified Core Web Vitals. This module intentionally does not execute JS.
"""
from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
from html import unescape
from ipaddress import ip_address
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import json
import re
import socket
import time
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

USER_AGENT = 'LensSEOAudit/1.0 (+local audit; respects robots.txt)'
TIMEOUT = (4, 11)
MAX_BYTES = 2_000_000
TRACKING = {'fbclid', 'gclid', 'msclkid', 'ref', 'source', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'}
HEADERS = {'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5'}
SESSION = requests.Session()
SESSION.trust_env = False

class AuditError(Exception):
    pass


def validate_public_url(raw: str) -> str:
    raw = (raw or '').strip()
    if not re.match(r'^https?://', raw, re.I):
        raw = 'https://' + raw
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise AuditError('Enter a valid website URL and port.') from exc
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise AuditError('Enter a valid public http(s) URL.')
    host = parsed.hostname.lower().rstrip('.')
    if host in {'localhost', 'localhost.localdomain'} or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid')):
        raise AuditError('Private or local network websites cannot be audited.')
    try:
        answers = socket.getaddrinfo(host, port or (443 if parsed.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise AuditError(f'Could not resolve website: {host}') from exc
    if not answers:
        raise AuditError('Website address could not be resolved.')
    for item in answers:
        address = ip_address(item[4][0].split('%')[0])
        if not address.is_global:
            raise AuditError('For safety, internal network addresses are blocked.')
    return normalize(raw)


def normalize(url: str) -> str:
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in TRACKING]
    path = p.path or '/'
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(query), ''))


def get_document(url: str, accept_types=('text/html', 'application/xhtml+xml', 'text/xml', 'application/xml', 'text/plain'), robots: RobotFileParser | None = None) -> tuple[requests.Response, str, float]:
    """Fetch text with small body limit, manual redirects and per-hop URL validation."""
    current = validate_public_url(url)
    for _ in range(5):
        if robots is not None and not robots.can_fetch(USER_AGENT, current):
            raise AuditError(f'Blocked by robots.txt: {current}')
        started = time.monotonic()
        try:
            res = SESSION.get(current, headers=HEADERS, timeout=TIMEOUT, allow_redirects=False, stream=True)
        except requests.RequestException as exc:
            raise AuditError(f'Unable to fetch {current}: {str(exc)[:150]}') from exc
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if res.is_redirect:
            target = res.headers.get('Location')
            res.close()
            if not target:
                raise AuditError('A redirect did not include a destination.')
            destination = validate_public_url(urljoin(current, target))
            if not same_site(url, destination):
                raise AuditError('Cross-site redirect was blocked; only the requested website is audited.')
            current = destination
            continue
        mime = res.headers.get('Content-Type', '').split(';')[0].lower()
        if mime and mime not in accept_types:
            res.close()
            raise AuditError('The website returned a non-HTML content type.')
        buf = bytearray()
        try:
            for chunk in res.iter_content(chunk_size=32_768):
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise AuditError('Page exceeds the 2 MB audit safety limit.')
        except requests.RequestException as exc:
            raise AuditError(f'Could not read the page: {str(exc)[:130]}') from exc
        finally:
            res.close()
        encoding = res.encoding or 'utf-8'
        return res, bytes(buf).decode(encoding, errors='replace'), elapsed_ms
    raise AuditError('Too many redirects.')


def same_site(root: str, url: str) -> bool:
    a = (urlsplit(root).hostname or '').removeprefix('www.')
    b = (urlsplit(url).hostname or '').removeprefix('www.')
    return a == b


def clean_text(s: str) -> str:
    return re.sub(r'\s+', ' ', unescape(s or '')).strip()


def parse_html(url: str, body: str, status: int, response_ms: int, depth: int, robots_header: str = '') -> dict:
    soup = BeautifulSoup(body, 'html.parser')
    title = clean_text(soup.title.get_text(' ', strip=True)) if soup.title else ''
    def meta(name: str) -> str:
        el = soup.find('meta', attrs={'name': re.compile('^'+re.escape(name)+'$', re.I)})
        return clean_text(el.get('content','')) if el else ''
    description = meta('description')
    robots = ' '.join([meta('robots'), meta('googlebot'), robots_header or '']).strip()
    h1s = [clean_text(x.get_text(' ',strip=True)) for x in soup.find_all('h1')]
    headings = [clean_text(x.get_text(' ',strip=True)) for x in soup.find_all(re.compile(r'^h[1-6]$'))]
    question_headings = [x for x in headings if x.endswith('?') or re.search(r'^(what|how|why|when|who|which|can|does|is|are|should|where)\b', x, re.I)]
    c = soup.find('link', attrs={'rel': lambda a: a and 'canonical' in str(a).lower()})
    canonical = urljoin(url, c.get('href','')) if c and c.get('href') else ''
    structured_types = set()
    broken_schema = False
    schema_payloads=[]
    for tag in soup.find_all('script', attrs={'type':re.compile(r'ld\+json', re.I)}):
        try:
            payload = json.loads(tag.string or tag.get_text())
            schema_payloads.append(payload)
        except (ValueError, TypeError):
            broken_schema=True
    def types_from(item):
        if isinstance(item, list):
            for child in item: types_from(child)
        elif isinstance(item, dict):
            kinds = item.get('@type', [])
            if isinstance(kinds, str): kinds=[kinds]
            structured_types.update(str(x) for x in kinds)
            # Course/Article can be nested inside @graph, mainEntity or itemListElement.
            for child in item.values():
                if isinstance(child, (dict, list)): types_from(child)
    for payload in schema_payloads: types_from(payload)
    main = soup.find('main') or soup.find('article') or soup.body or soup
    main_copy = BeautifulSoup(str(main), 'html.parser')
    for elem in main_copy(['script','style','nav','footer','header','noscript']): elem.decompose()
    text = clean_text(main_copy.get_text(' ',strip=True))
    words = text.split()
    paras = [clean_text(p.get_text(' ',strip=True)) for p in main_copy.find_all('p')]
    first_para = next((x for x in paras if len(x.split()) >= 15), '')
    links=[]
    for a in soup.find_all('a',href=True):
        href=a.get('href','')
        if not href or href.startswith(('#','mailto:','tel:','javascript:','data:')): continue
        absolute = normalize(urljoin(url, href))
        if urlsplit(absolute).scheme in ('http','https'):
            links.append({'url':absolute,'text':clean_text(a.get_text(' ',strip=True))[:100]})
    imgs = soup.find_all('img')
    img_missing_alt = sum(1 for x in imgs if x.get('alt') is None)
    author = bool(soup.find('meta',attrs={'name':re.compile('^author$',re.I)}) or soup.find(attrs={'rel':'author'}) or soup.find(attrs={'itemprop':'author'}) or soup.find(class_=re.compile(r'author|byline',re.I)))
    updated = bool(soup.find('meta',attrs={'property':re.compile('article:modified_time',re.I)}) or soup.find('time',attrs={'datetime':True}) or re.search(r'last updated|updated on|modified on', text, re.I))
    return {
        'url':url, 'status':status, 'response_ms':response_ms,'depth':depth,
        'title':title,'description':description,'h1':h1s,'headings':headings[:70],
        'question_headings':question_headings[:12], 'canonical':canonical,'robots':robots,
        'word_count':len(words),'first_paragraph':first_para[:450], 'body_excerpt':text[:4000],
        'links':links, 'images':len(imgs), 'images_missing_alt':img_missing_alt,
        'schema_types':sorted(structured_types), 'invalid_schema':broken_schema,
        'author_detected':author,'updated_detected':updated,
        'faq_detected':bool(re.search(r'frequently asked questions|\bfaq(s)?\b',text, re.I) or len(question_headings)>=3),
        'inlinks':0, 'inlink_sources':[],
    }


def robot_policy(root: str) -> tuple[RobotFileParser|None,list[str],list[str]]:
    warnings=[]
    robots_url = urljoin(root, '/robots.txt')
    try:
        res, content, _ = get_document(robots_url)
        if res.status_code == 200:
            rp=RobotFileParser(); rp.parse(content.splitlines())
            sitemaps=re.findall(r'^\s*Sitemap:\s*(\S+)',content,flags=re.I|re.M)
            return rp,sitemaps,warnings
        if res.status_code in (404, 410):
            warnings.append('robots.txt not present (HTTP 404/410); no robots policy was applied.')
            return None, [], warnings
        raise AuditError(f'robots.txt returned HTTP {res.status_code}; audit stopped rather than assuming crawling is allowed.')
    except AuditError as exc:
        raise AuditError(f'Cannot verify robots.txt: {exc}') from exc


def sitemap_urls(root: str, listed: list[str], cap: int=70, robots: RobotFileParser | None = None) -> list[str]:
    urls=[]
    targets=listed[:3] if listed else [urljoin(root,'/sitemap.xml')]
    explored=set()
    for _ in range(2):
        next_targets=[]
        for address in targets:
            if len(urls)>=cap: break
            if address in explored or not same_site(root,address): continue
            explored.add(address)
            try:
                response,xml,_=get_document(address, robots=robots)
                if response.status_code != 200: continue
                root_element=ET.fromstring(xml)
                namespace={'sm':'http://www.sitemaps.org/schemas/sitemap/0.9'}
                if root_element.tag.endswith('sitemapindex'):
                    next_targets.extend(el.text for el in root_element.findall('.//sm:sitemap/sm:loc',namespace) if el.text)
                else:
                    for el in root_element.findall('.//sm:url/sm:loc',namespace):
                        if el.text: urls.append(normalize(el.text))
            except (AuditError,ET.ParseError): continue
        targets=next_targets[:5]
        if not targets:break
    return [x for x in dict.fromkeys(urls) if same_site(root,x) and (robots is None or robots.can_fetch(USER_AGENT,x))][:cap]


REPORT_COLUMNS = ('URL', 'Issue', 'SEO/AEO/GEO', 'Priority', 'Recommended Fix')


def report_rows(findings: list[dict]) -> list[dict]:
    """The single source of truth for the assignment's exact five-column report."""
    return [{
        'URL': finding['url'], 'Issue': finding['issue'],
        'SEO/AEO/GEO': finding['category'], 'Priority': finding['priority'],
        'Recommended Fix': finding['fix'],
    } for finding in findings]


def in_scope(root: str, url: str, section_only: bool = True) -> bool:
    if urlsplit(url).scheme not in ('http', 'https') or not same_site(root, url):
        return False
    if not section_only:
        return True
    start_path = urlsplit(root).path.rstrip('/')
    if not start_path:
        return True
    candidate_path = urlsplit(url).path.rstrip('/')
    return candidate_path == start_path or candidate_path.startswith(start_path + '/')


def issue(page: dict, category: str, check: str, label: str, priority: str, fix: str, evidence: str='', effort: str='Low') -> dict:
    return {'url':page['url'],'issue':label,'category':category,'check':check,'priority':priority,
            'fix':fix,'evidence':evidence,'effort':effort}


def analyze(pages: list[dict], root_url: str, all_sitemap_urls: list[str], competitor_pages: list[dict]|None=None) -> tuple[list[dict],dict,dict]:
    findings=[]
    for p in pages:
        dest=set(l['url'] for l in p['links'] if same_site(root_url,l['url']))
        p['outlink_count']=len(dest)
        p['inlink_sources']=[other['url'] for other in pages if other['url']!=p['url'] and p['url'] in {x['url'] for x in other['links']}]
        p['inlinks']=len(p['inlink_sources'])
        tokens = set(re.split(r'[,;\s]+', p['robots'].lower()))
        p['indexing']='Noindex detected' if 'noindex' in tokens else 'Not flagged by page robots'
        p['direct_answer']=40 <= len(p['first_paragraph'].split()) <= 75
    titles=Counter(p['title'].lower() for p in pages if p['title'])
    descs=Counter(p['description'].lower() for p in pages if p['description'])
    for p in pages:
        is_course=bool(re.search(r'course|batches|coaching|program',urlsplit(p['url']).path,re.I))
        is_article=bool(re.search(r'blog|article|guide|exams|cutoff|syllabus|tips',urlsplit(p['url']).path,re.I))
        if p['status'] >= 400:
            findings.append(issue(p,'SEO','Indexing','HTTP error '+str(p['status']),'High','Fix the URL or redirect it to the nearest relevant live page.',f'HTTP {p["status"]}'))
            continue
        directives = set(re.split(r'[,;\s]+', p['robots'].lower()))
        if 'noindex' in directives:
            findings.append(issue(p,'SEO','Indexing','Noindex directive detected','High','If this page should appear in search, verify and remove its noindex directive.',p['robots']))
        if directives.intersection({'no-index','no-follow'}):
            findings.append(issue(p,'SEO','Indexing','Non-standard robots directive','Medium','Verify the intended indexing behavior. Use standard values such as noindex or nofollow, not no-index/no-follow.',p['robots']))
        if not p['canonical']:
            findings.append(issue(p,'SEO','Indexing','Canonical URL not found','Medium','Add a canonical tag pointing to the preferred indexable version, if appropriate.'))
        elif normalize(p['canonical'])!=normalize(p['url']):
            findings.append(issue(p,'SEO','Indexing','Canonical points elsewhere','Medium','Confirm this canonicalization is intentional.',p['canonical']))
        if not p['title']:
            findings.append(issue(p,'SEO','On-page','Missing title tag','High','Write a unique descriptive title that matches the page intent.'))
        elif len(p['title']) > 65:
            findings.append(issue(p,'SEO','On-page','Long title tag','Low','Shorten the title if needed while keeping the main topic clear.',f'{len(p["title"])} characters'))
        if p['title'] and titles[p['title'].lower()]>1:
            findings.append(issue(p,'SEO','On-page','Duplicate page title','Medium','Write a distinct title matching this page’s specific purpose.',p['title']))
        if not p['description'] or p['description'].lower() in ('default description','page description'):
            findings.append(issue(p,'SEO','On-page','Missing or placeholder meta description','Medium','Write a unique summary of this page’s value and content.',p['description'] or 'Not found'))
        elif len(p['description'])>175:
            findings.append(issue(p,'SEO','On-page','Long meta description','Low','Keep the most useful message toward the beginning; Google may rewrite snippets.',f'{len(p["description"])} characters'))
        if p['description'] and descs[p['description'].lower()]>1:
            findings.append(issue(p,'SEO','On-page','Duplicate meta description','Low','Tailor the description to this page’s content.'))
        if len(p['h1'])==0:
            findings.append(issue(p,'SEO','On-page','Missing H1 heading','High' if is_course else 'Medium','Add a clear H1 describing the topic or course.'))
        elif len(p['h1'])>1:
            findings.append(issue(p,'SEO','On-page','Multiple H1 headings','Low','Review heading hierarchy for clarity; multiple H1s are not inherently an error.'))
        if p['images_missing_alt']:
            findings.append(issue(p,'SEO','On-page','Image alt attributes missing','Low','Add meaningful alt text to informative images; use empty alt for decorative images.',f'{p["images_missing_alt"]} images'))
        if p['inlinks']==0 and len(pages)>1 and p['url']!=root_url:
            findings.append(issue(p,'SEO','Internal links','No inbound links within crawl sample','Medium','Add contextual links from the relevant category hub and related articles.', 'Limited sample: not proof of an orphan page'))
        if p['depth']>=3:
            findings.append(issue(p,'SEO','Internal links','Deep crawl path','Low','Link important pages from relevant hubs to make discovery easier.',f'Crawl depth {p["depth"]}'))
        if p['invalid_schema']:
            findings.append(issue(p,'SEO','Schema','Invalid JSON-LD syntax','Medium','Correct JSON syntax and validate structured data.'))
        if is_course and 'Course' not in p['schema_types']:
            findings.append(issue(p,'SEO','Schema','Course schema not detected','Low','Consider accurate Course markup if the page describes a specific educational course; validate eligibility.'))
        if is_article and not any(t in p['schema_types'] for t in ('Article','BlogPosting','NewsArticle')):
            findings.append(issue(p,'SEO','Schema','Article schema not detected','Low','Consider accurate Article markup with author and dates where applicable.'))
        if p['word_count']<280:
            findings.append(issue(p,'SEO/AEO','Content','Limited extractable body content','Medium','Check if main content is rendered with JavaScript; if genuinely thin, add useful original details.',f'{p["word_count"]} extracted words'))
        if is_article and not p['author_detected']:
            findings.append(issue(p,'GEO','Trust','No clear author signal detected','Low','Include an accurate author/byline when editorially appropriate.'))
        if is_article and not p['updated_detected']:
            findings.append(issue(p,'GEO','Trust','No clear update-date signal detected','Low','For time-sensitive articles, show a truthful updated date and keep facts current.'))
        if not p['direct_answer'] and (is_article or p['word_count']>=500):
            findings.append(issue(p,'AEO','Answer readiness','Opening summary may need improvement','Medium','Review whether an accurate, concise answer near the start would help this page’s search intent.',f'First substantial paragraph: {len(p["first_paragraph"].split())} words'))
        if is_article and not p['question_headings']:
            findings.append(issue(p,'AEO','Answer readiness','No question-based headings detected','Low','Answer relevant learner questions under clear headings where useful.'))
        if p['response_ms']>1700:
            findings.append(issue(p,'SEO','Performance','Slow initial HTML response','Medium','Investigate backend response time and confirm performance with PageSpeed Insights.',f'{p["response_ms"]} ms server fetch (not CWV)'))
    if competitor_pages and pages:
        primary=pages[0]
        own=set(re.findall(r'[a-z]{4,}', ' '.join(primary['headings']).lower()))
        stops={'what','when','where','which','this','that','with','from','your','their','have','about','guide','course','best','page'}
        candidate=[]
        for c in competitor_pages:
            for h in c['headings'][:30]:
                words={x for x in re.findall(r'[a-z]{4,}', h.lower()) if x not in stops}
                if len(words)>=2 and len(words-own)>=2: candidate.append(h)
        if candidate:
            labels=list(dict.fromkeys(candidate))[:5]
            findings.append(issue(primary,'AEO/GEO','Content gaps','Potential competitor topic gaps','Medium',
                                  'Review these competitor sections and add genuinely useful coverage where relevant: '+ '; '.join(labels),
                                  f'Compared with {len(competitor_pages)} competitor URL(s)','High'))
    # Explicit, explainable priority formula: impact + likely visibility gain - effort.
    # This is a planning heuristic, not an observed traffic uplift prediction.
    repetition=Counter(f['issue'] for f in findings)
    for f in findings:
        initial=f['priority']
        impact={'High':5,'Medium':3,'Low':2}[initial]
        if re.search(r'course|batches|coaching|enroll|admission',urlsplit(f['url']).path,re.I):
            impact=min(5, impact+1)
        gain={'High':5,'Medium':3,'Low':2}[initial]
        effort={'Low':1,'Medium':3,'High':5}.get(f['effort'],1)
        recurring=1 if repetition[f['issue']]>=3 else 0
        score=2*impact+2*gain-effort+recurring
        f['impact']=impact
        f['visibility_gain']=gain
        f['priority_score']=score
        f['priority']='High' if score>=15 else 'Medium' if score>=10 else 'Low'
    high=sum(1 for f in findings if f['priority']=='High')
    medium=sum(1 for f in findings if f['priority']=='Medium')
    low=sum(1 for f in findings if f['priority']=='Low')
    # Transparent heuristic category deductions; never claim SEO/AI citation probability.
    penalties={'SEO':0,'AEO':0,'GEO':0}
    for f in findings:
        d={'High':11,'Medium':5,'Low':2}[f['priority']]
        for cat in penalties:
            if cat in f['category'].split('/'):
                penalties[cat]+=d
    denominator=max(1,len(pages))
    score={k:max(0,round(100 - penalty/denominator)) for k,penalty in penalties.items()}
    stats={'pages':len(pages),'issues':len(findings),'high':high,'medium':medium,'low':low,
           'scores':score,'checks':['Indexing & crawlability','On-page tags','Internal linking',
                 'Schema markup','Content quality & freshness','AI answer readiness','Performance & accessibility'],
           'score_note':'Heuristic readiness scores (0–100) reflect issue weights per crawled page. Priority score uses estimated impact, visibility gain and effort; it is not a traffic forecast. Scores are NOT Google rankings, Core Web Vitals or AI citation probabilities.'}
    return sorted(findings,key=lambda x:({'High':0,'Medium':1,'Low':2}[x['priority']],x['url'],x['check'])),stats,score


def crawl_site(start_url: str,max_pages: int=12,competitor_urls: list[str]|None=None,section_only: bool=True) -> dict:
    root=validate_public_url(start_url)
    max_pages=max(1,min(max_pages,35))
    rp,sitemap_candidates,policy_warnings=robot_policy(root)
    known_urls=sitemap_urls(root,sitemap_candidates,cap=100,robots=rp)
    queue=deque([(root,0)])
    seen=set()
    pages=[]
    warnings=list(policy_warnings)
    if section_only and urlsplit(root).path.strip('/'):
        warnings.append('Section-only scan: URLs outside the entered path were excluded.')
    # Prioritize navigational links; sitemap fills in remaining slots.
    sitemap_queue=deque((u,1) for u in known_urls if normalize(u)!=root and in_scope(root,u,section_only))
    failures=[]
    while len(pages)<max_pages and (queue or sitemap_queue):
        if queue:
            address,depth=queue.popleft()
        else:
            address,depth=sitemap_queue.popleft()
        if address in seen:continue
        seen.add(address)
        if not in_scope(root,address,section_only):continue
        if rp and not rp.can_fetch(USER_AGENT,address):
            warnings.append(f'Skipped by robots.txt: {address}')
            continue
        try:
            res,body,ms=get_document(address, robots=rp)
            final=normalize(res.url)
            if not in_scope(root,final,section_only):
                warnings.append(f'Skipped out-of-scope redirect: {address}'); continue
            if final != address and final in seen:
                continue
            seen.add(final)
            page=parse_html(final,body,res.status_code,ms,depth,res.headers.get('X-Robots-Tag',''))
            pages.append(page)
            for link in page['links']:
                link_url=link['url']
                if in_scope(root,link_url,section_only) and link_url not in seen and not re.search(r'\.(?:pdf|png|jpg|svg|jpeg|gif|webp|css|js|xml|zip|mp4)$',urlsplit(link_url).path,re.I):
                    if depth<=2 and len(queue)<120: queue.append((link_url,depth+1))
        except AuditError as exc:
            failures.append({'url':address,'error':str(exc)})
        if len(pages)>=max_pages:break
    if not pages:
        raise AuditError('No HTML pages could be audited. The site may block bots, require JavaScript, or restrict crawling.')
    competitors=[]
    for raw in (competitor_urls or [])[:3]:
        try:
            competitor_url=validate_public_url(raw)
            competitor_robots,_,_=robot_policy(competitor_url)
            res,body,ms=get_document(competitor_url, robots=competitor_robots)
            if res.status_code==200:competitors.append(parse_html(res.url,body,res.status_code,ms,0))
        except AuditError as exc:
            warnings.append(f'Competitor not analyzed: {raw} ({str(exc)[:80]})')
    findings,stats,_=analyze(pages,root,known_urls,competitors)
    return {'url':root,'audited_at':datetime.now(timezone.utc).isoformat(),'stats':stats,'pages':pages,
            'findings':findings,'warnings':list(dict.fromkeys(warnings))[:12], 'failed_urls':failures[:12],
            'competitor_count':len(competitors), 'sitemap_urls_seen':len(known_urls), 'section_only':section_only,
            'report_columns':list(REPORT_COLUMNS), 'report_rows':report_rows(findings),
            'ai_status':'AI suggestions are off. Enable them with OPENAI_API_KEY and the AI toggle.'}
