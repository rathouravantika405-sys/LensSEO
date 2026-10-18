"""LensSEO local web app API."""
from pathlib import Path
import os
import time
from collections import deque
from threading import BoundedSemaphore, Lock
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audit import crawl_site, AuditError, REPORT_COLUMNS, report_rows
from .ai import enrich_with_ai

PUBLIC_MODE = os.getenv('LENSSEO_PUBLIC_MODE', '').lower() in ('true','1','yes')
ALLOWED_DOMAINS = {x.strip().lower() for x in os.getenv('LENSSEO_ALLOWED_DOMAINS', '').split(',') if x.strip()}
ACTIVE_SCANS = BoundedSemaphore(value=2)
RATE_LOCK = Lock()
RECENT_SCANS = deque()

def is_allowed_target(raw_url: str) -> bool:
    if not PUBLIC_MODE: return True
    url = raw_url.strip()
    if '://' not in url: url = 'https://' + url
    try: host = (urlsplit(url).hostname or '').lower().rstrip('.')
    except ValueError: return False
    return bool(ALLOWED_DOMAINS) and any(host == domain or host == 'www.'+domain for domain in ALLOWED_DOMAINS)

app=FastAPI(title='LensSEO',description='Bounded technical SEO, AEO & GEO audit with optional AI recommendations',version='1.1')
STATIC=Path(__file__).resolve().parent/'static'
app.mount('/static',StaticFiles(directory=str(STATIC)),name='static')

class AuditRequest(BaseModel):
    url: str=Field(min_length=4,max_length=400)
    max_pages:int=Field(default=12,ge=1,le=35)
    competitor_urls:list[str]=Field(default_factory=list,max_length=3)
    use_ai:bool=False
    section_only:bool=True

@app.get('/')
def index():
    return FileResponse(STATIC/'index.html')

@app.get('/api/health')
def health():
    return {'ok':True,'ai_configured':bool(os.environ.get('OPENAI_API_KEY')) and not PUBLIC_MODE, 'public_demo':PUBLIC_MODE}

@app.post('/api/audit')
def audit(req:AuditRequest, request:Request):
    if PUBLIC_MODE:
        if not is_allowed_target(req.url) or any(not is_allowed_target(u) for u in req.competitor_urls):
            raise HTTPException(status_code=403,detail='Public demo scans are limited to the listed education websites. Try a PW site or the sample report.')
        if not ALLOWED_DOMAINS:
            raise HTTPException(status_code=503,detail='Public demo targets are not configured.')
        if not ACTIVE_SCANS.acquire(blocking=False):
            raise HTTPException(status_code=429,detail='Two scans are already running. Please try again shortly.')
        with RATE_LOCK:
            now = time.monotonic()
            while RECENT_SCANS and RECENT_SCANS[0] < now - 3600: RECENT_SCANS.popleft()
            if len(RECENT_SCANS) >= 30:
                ACTIVE_SCANS.release()
                raise HTTPException(status_code=429,detail='The public demo has reached its hourly scan limit. Please try later.')
            RECENT_SCANS.append(now)
    try:
        try:
            result=crawl_site(req.url,min(req.max_pages,8) if PUBLIC_MODE else req.max_pages,
                              req.competitor_urls if not PUBLIC_MODE else [],req.section_only)
        except AuditError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        except Exception:
            raise HTTPException(status_code=500,detail='The crawl encountered an unexpected error. Check logs and try again.')
        if req.use_ai and not PUBLIC_MODE:
            try:
                result['ai_status']=enrich_with_ai(result)
            except Exception:
                result['ai_status']='AI suggestions could not be generated. The rule-based audit completed successfully.'
        if PUBLIC_MODE:
            result['ai_status']='AI rewrites are disabled in the free public demo. The rule-based audit is operational.'
        return result
    finally:
        if PUBLIC_MODE: ACTIVE_SCANS.release()

@app.get('/api/demo')
def demo():
    rows=[
        ('/courses/data-analytics','Placeholder meta description','SEO','High','Write a useful, unique description explaining the course.'),
        ('/blog/neet-cutoff','No concise answer at the top','AEO','Medium','Add a factual, direct answer and a category-wise table.'),
        ('/courses/full-stack','Missing H1 heading','SEO','High','Add a single descriptive H1 that reflects the course.'),
        ('/blog/career-roadmap','No author byline detected','GEO','Low','Add a real author with relevant expertise if appropriate.'),
        ('/courses/ai','No Course markup detected','SEO','Low','Check Course schema suitability and include accurate fields.'),
        ('/blog/neet-syllabus','No inbound links in crawl sample','SEO','Medium','Add relevant contextual links from related hubs.'),
    ]
    origin='https://example.org'
    findings=[{'url':origin+u,'issue':label,'category':cat,'check':'Sample','priority':pri,
               'fix':fix,'evidence':'Demonstration only — not a live audit','effort':'Low'} for u,label,cat,pri,fix in rows]
    return {
        'report_columns':list(REPORT_COLUMNS), 'report_rows':report_rows(findings),
        'url':origin,'audited_at':'Sample data only','is_demo':True,
        'stats':{'pages':5,'issues':len(findings),'high':2,'medium':2,'low':2,
             'scores':{'SEO':74,'AEO':68,'GEO':61}, 'checks':['Indexing & crawlability','On-page tags','Internal linking','Schema markup','Content quality & freshness','AI answer readiness','Performance & accessibility'],
             'score_note':'Sample scores only — not from a real website.'},
        'pages':[], 'findings':findings,'warnings':['Demo data is illustrative. Run an audit to get real findings.'],
        'failed_urls':[],'competitor_count':0,'sitemap_urls_seen':0,
        'section_only':True, 'ai_status':'Demo: no AI call was made.'
    }


@app.middleware('http')
async def response_safety_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store'
    return response
