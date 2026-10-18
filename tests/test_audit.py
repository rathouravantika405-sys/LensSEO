from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.audit import parse_html, analyze, normalize, validate_public_url, AuditError

client=TestClient(app)

HTML='''<!doctype html><html><head><title>NEET Exam Guide</title><meta name="description" content="Page description"><link rel="canonical" href="https://example.org/neet"></head><body><main><h1>NEET Guide</h1><h2>What is NEET?</h2><p>The National Eligibility cum Entrance Test is an entrance examination for prospective medical students. This introduction covers some of the basic information about eligibility and how the examination is organized.</p><a href="/neet/books">Books</a></main></body></html>'''

def test_parse_html():
    page=parse_html('https://example.org/neet',HTML,200,321,0)
    assert page['title']=='NEET Exam Guide'
    assert page['h1']==['NEET Guide']
    assert page['schema_types']==[]
    assert page['links'][0]['url']=='https://example.org/neet/books'
    assert page['question_headings']==['What is NEET?']

def test_analyze_finds_placeholder_description_and_thin_text():
    page=parse_html('https://example.org/neet',HTML,200,321,0)
    issues,stats,_=analyze([page],page['url'],[])
    labels=[row['issue'] for row in issues]
    assert 'Missing or placeholder meta description' in labels
    assert 'Limited extractable body content' in labels
    assert stats['pages']==1
    assert stats['scores']['SEO']<=100

def test_url_normalization():
    assert normalize('https://example.org/neet?utm_source=x&foo=bar#section')=='https://example.org/neet?foo=bar'

def test_private_urls_blocked():
    for url in ['http://127.0.0.1:8000','http://localhost/','http://10.2.2.2','http://169.254.169.254']:
        with pytest.raises(AuditError):validate_public_url(url)

def test_demo_is_explicit_and_downloadable_shape():
    response=client.get('/api/demo')
    assert response.status_code==200
    data=response.json()
    assert data['is_demo']
    assert len(data['findings'])>=5
    assert {'url','issue','category','priority','fix'}<=data['findings'][0].keys()

def test_bad_url_returns_422():
    response=client.post('/api/audit',json={'url':'http://127.0.0.1:8080','max_pages':1})
    assert response.status_code==422

def test_homepage_served():
    response=client.get('/')
    assert response.status_code==200
    assert 'LensSEO' in response.text


def test_full_crawl_with_fixture_html(monkeypatch):
    import app.audit as audit
    from requests import Response
    links='''<html><head><title>Course X</title><meta name="description" content="Default description"></head>
       <body><a href="/course-x">Course X</a><h1>Welcome</h1></body></html>'''
    second='''<html><head><title>Course X</title></head><body><main><h2>Curriculum</h2>
       <p>Short course description.</p></main></body></html>'''
    class FakeResponse:
        status_code=200
        headers={}
        def __init__(self,url):self.url=url
    def fake_document(url,*args,**kwargs):
        if url.endswith('/robots.txt'):return FakeResponse(url),'User-agent: *\nAllow: /',12
        if url.endswith('/sitemap.xml'):return FakeResponse(url),'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/course-x</loc></url></urlset>',12
        return FakeResponse(url),(second if url.endswith('/course-x') else links),42
    monkeypatch.setattr(audit,'validate_public_url',lambda x:x)
    monkeypatch.setattr(audit,'get_document',fake_document)
    result=audit.crawl_site('https://example.com/',2)
    assert result['stats']['pages']==2
    assert any(i['issue']=='Missing H1 heading' for i in result['findings'])
    assert all('priority_score' in x for x in result['findings'])
    assert all(x['priority'] in {'High','Medium','Low'} for x in result['findings'])


def test_report_follows_exact_five_column_assignment_format():
    data=client.get('/api/demo').json()
    expected=['URL','Issue','SEO/AEO/GEO','Priority','Recommended Fix']
    assert data['report_columns']==expected
    assert len(data['report_rows'])>=5
    assert all(list(item)==expected for item in data['report_rows'])
    assert all(item['Issue'] and item['Recommended Fix'] for item in data['report_rows'])


def test_nonstandard_no_index_is_not_claimed_to_block_indexing():
    doc='''<html><head><title>Test</title><meta name="robots" content="no-index;no-follow"></head><body><main><h1>Test</h1></main></body></html>'''
    page=parse_html('https://example.org/neet/test-series',doc,200,20,0)
    rows,_,_=analyze([page],page['url'],[])
    assert any(x['issue']=='Non-standard robots directive' for x in rows)
    assert not any(x['issue']=='Noindex directive detected' for x in rows)


def test_section_scope_keeps_neet_and_excludes_other_exams(monkeypatch):
    import app.audit as audit
    class Response:
        status_code=200
        headers={}
        def __init__(self,url):self.url=url
    calls=[]
    def fetch(url,*args,**kwargs):
        calls.append(url)
        if url.endswith('/robots.txt'):
            return Response(url), 'User-agent: *\nAllow: /',15
        if url.endswith('/sitemap.xml'):
            xml='''<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.org/jee</loc></url><url><loc>https://example.org/neet/syllabus</loc></url></urlset>'''
            return Response(url),xml,15
        return Response(url),'<html><head><title>NEET</title></head><body><h1>NEET</h1><a href="/jee">JEE</a><a href="/neet/books">NEET books</a></body></html>',15
    monkeypatch.setattr(audit,'validate_public_url',lambda url:url)
    monkeypatch.setattr(audit,'get_document',fetch)
    output=audit.crawl_site('https://example.org/neet',max_pages=3,section_only=True)
    scanned={p['url'] for p in output['pages']}
    assert scanned=={'https://example.org/neet','https://example.org/neet/books','https://example.org/neet/syllabus'}
    assert 'https://example.org/jee' not in calls
    assert output['section_only'] is True


def test_robots_policy_fails_closed_when_inaccessible(monkeypatch):
    import app.audit as audit
    class Response:
        status_code=403
    monkeypatch.setattr(audit,'get_document',lambda *args,**kwargs:(Response(),'No access',1))
    with pytest.raises(AuditError,match='robots.txt'):
        audit.robot_policy('https://example.org/')


def test_nested_json_ld_types_are_detected():
    html='''<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","mainEntity":{"@type":"Course","name":"Science"}}</script></head><body><main>Course information</main></body></html>'''
    page=parse_html('https://example.org/course',html,200,1,0)
    assert {'WebPage','Course'}.issubset(set(page['schema_types']))


def test_cross_site_redirect_rejected_before_fetching_target(monkeypatch):
    import app.audit as audit
    class Response:
        is_redirect=True
        headers={'Location':'https://evil.example/path'}
        def close(self):pass
    calls=[]
    monkeypatch.setattr(audit,'validate_public_url',lambda value:value)
    def fake_get(url,**kwargs):
        calls.append(url)
        return Response()
    monkeypatch.setattr(audit.SESSION,'get',fake_get)
    with pytest.raises(AuditError, match='Cross-site redirect'):
        audit.get_document('https://example.org/page')
    assert calls==['https://example.org/page']


def test_response_safety_headers():
    response=client.get('/api/demo')
    assert response.headers['Cache-Control']=='no-store'
    assert response.headers['X-Content-Type-Options']=='nosniff'
    assert response.headers['X-Frame-Options']=='DENY'


def test_api_audit_delivers_five_column_report(monkeypatch):
    from app import main
    from app.audit import parse_html, analyze, report_rows
    doc='''<html><head><title>Course page</title><meta name="description" content="Default description"></head><body><main><p>Some text</p></main></body></html>'''
    p=parse_html('https://example.org/course',doc,200,22,0)
    findings,stats,_=analyze([p],p['url'],[])
    def fake_crawl(url,max_pages,competitors,section_only):
        assert url=='https://example.org/course'
        assert section_only is True
        return {'url':url,'audited_at':'2026-10-19T00:00:00+00:00','stats':stats,'pages':[p],
                'findings':findings,'warnings':[],'failed_urls':[],'competitor_count':0,
                'sitemap_urls_seen':0,'report_columns':['URL','Issue','SEO/AEO/GEO','Priority','Recommended Fix'],
                'report_rows':report_rows(findings),'ai_status':'Off'}
    monkeypatch.setattr(main,'crawl_site',fake_crawl)
    r=client.post('/api/audit',json={'url':'https://example.org/course','max_pages':1})
    assert r.status_code==200
    obj=r.json()
    assert len(obj['report_rows'])==len(obj['findings'])
    assert obj['report_rows'][0]['URL']=='https://example.org/course'
    assert list(obj['report_rows'][0])==obj['report_columns']


def test_invalid_hostname_format_gives_validation_error_not_server_error():
    res=client.post('/api/audit',json={'url':'https://[invalid','max_pages':1})
    assert res.status_code==422
