"""Optional evidence-anchored model-based suggestions for LensSEO.

The model never generates observed crawl measurements. It only rewrites
recommendations and identifies potential topical gaps from supplied excerpts.
"""
import json
import os


def enrich_with_ai(report: dict) -> str:
    token=os.environ.get('OPENAI_API_KEY','').strip()
    if not token:
        return 'No OPENAI_API_KEY configured. Rule-based findings and scores are available.'
    from openai import OpenAI
    client=OpenAI(api_key=token,timeout=40,max_retries=1)
    pages=report['pages'][:5]
    findings=report['findings'][:12]
    context={
        'pages':[{'url':p['url'],'title':p['title'],'h1':p['h1'],'headings':p['headings'][:16],
                  'excerpt':p['body_excerpt'][:1100],'description':p['description']} for p in pages],
        'issues':[{'url':f['url'],'issue':f['issue'],'fix':f['fix'],'evidence':f['evidence']} for f in findings]
    }
    system=('You are an experienced SEO editor assisting a human. Analyze ONLY the supplied crawled content and findings. '
            'Never invent search rankings, traffic, Google eligibility, competitors, factual course claims, or AI citations. '
            'Return valid JSON with keys: summary (string <=60 words), opportunities (array of up to 5 strings), '
            'rewrites (array of objects with url, proposed_title, proposed_meta, suggested_opening_answer). '
            'Keep concrete proposed text brief. A suggestion is not a verified crawl fact. '
            'Only rewrite pages that exist in input; do not make up facts absent in excerpts.')
    response=client.chat.completions.create(model=os.environ.get('LENSSEO_AI_MODEL','gpt-4.1-mini'),
        messages=[{'role':'system','content':system},{'role':'user','content':json.dumps(context,ensure_ascii=False)}],
        response_format={'type':'json_object'},temperature=0.2,max_tokens=1100)
    content=response.choices[0].message.content or '{}'
    try: obj=json.loads(content)
    except json.JSONDecodeError:
        return 'AI returned an unreadable response. The rule-based audit remains available.'
    valid_urls={p['url'] for p in pages}
    report['ai_suggestions']={
        'summary':str(obj.get('summary',''))[:600],
        'opportunities':[str(x)[:280] for x in obj.get('opportunities',[])[:5] if isinstance(x,str)],
        'rewrites':[
            {'url':x.get('url'), 'proposed_title':str(x.get('proposed_title',''))[:200],
             'proposed_meta':str(x.get('proposed_meta',''))[:350],
             'suggested_opening_answer':str(x.get('suggested_opening_answer',''))[:900]}
            for x in obj.get('rewrites',[])[:5] if isinstance(x,dict) and x.get('url') in valid_urls
        ]
    }
    return 'AI suggestions generated from the crawled page excerpts. Human review required.'
