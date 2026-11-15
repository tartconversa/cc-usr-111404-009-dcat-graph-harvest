"""授权目录采集：按来源入口、分页链接与声明的图范围取得数据集及其分发关系。

只跟随HTTP Link rel=next分页；目录中的任意IRI（许可、accessURL、downloadURL、
sameAs等）只记录不抓取。accessURL与downloadURL分别保留，不并成无角色链接集合；
缺许可或访问方式如实标记missing，不替来源补声明；owl:sameAs只记录不合并。
部分页面失败时交付已有资料与未闭合关系，complete=false，不宣称目录齐全。"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from .rdf import RDF_TYPE,BNode,Iri,Literal,ParseError,UnsupportedMedia,parse_response,term_json
from .source import fetch
from .store import GraphStore,document_scope

DCAT='http://www.w3.org/ns/dcat#'
DCT='http://purl.org/dc/terms/'
OWL_SAMEAS=Iri('http://www.w3.org/2002/07/owl#sameAs')
P_TYPE=Iri(RDF_TYPE)
P_DISTRIBUTION=Iri(DCAT+'distribution')
P_ACCESS=Iri(DCAT+'accessURL')
P_DOWNLOAD=Iri(DCAT+'downloadURL')
P_TITLE=Iri(DCT+'title')
P_LICENSE=Iri(DCT+'license')
T_DATASET=Iri(DCAT+'Dataset')
DEFAULT_ACCEPT='text/turtle, application/n-quads;q=0.9, application/n-triples;q=0.8'

def _origin_of(url):
    try:parts=urlsplit(url)
    except ValueError:return None
    scheme=parts.scheme.lower()
    host=(parts.hostname or '').lower()
    if not scheme or not host:return None
    try:port=parts.port
    except ValueError:return None
    default={'http':80,'https':443}.get(scheme)
    netloc=host if port is None or port==default else f'{host}:{port}'
    return f'{scheme}://{netloc}'

@dataclass(frozen=True)
class Scope:
    origin:str
    path_prefix:str='/'
    def __post_init__(self):
        origin=_origin_of(self.origin)
        if origin is None:raise ValueError('范围源点无效')
        object.__setattr__(self,'origin',origin)
        if not self.path_prefix.startswith('/'):raise ValueError('范围路径前缀无效')
    def allows(self,url):
        try:path=urlsplit(url).path
        except ValueError:return False
        return _origin_of(url)==self.origin and path.startswith(self.path_prefix)

def _split_link_value(value):
    parts=[];buf=[];angle=False;quote=False
    for char in value:
        if char=='<' and not quote:angle=True
        elif char=='>' and not quote:angle=False
        elif char=='"' and not angle:quote=not quote
        if char==',' and not angle and not quote:
            parts.append(''.join(buf));buf=[]
        else:buf.append(char)
    parts.append(''.join(buf))
    return parts

_LINK_ITEM=re.compile(r'\s*<([^>]*)>([^>]*)$')
_LINK_PARAM=re.compile(r';\s*([\w-]+)\s*=\s*(?:"([^"]*)"|([^;]*))')

def next_links(headers):
    out=[]
    for key,value in headers:
        if key.lower()!='link':continue
        for item in _split_link_value(value):
            match=_LINK_ITEM.match(item)
            if not match:continue
            rels=set()
            for param in _LINK_PARAM.finditer(match.group(2)):
                if param.group(1).lower()=='rel':
                    rels.update(rel.lower() for rel in (param.group(2) or param.group(3) or '').split())
            if 'next' in rels:out.append(match.group(1))
    return out

def harvest(entry,source_id,scope,*,fetcher=None,store=None,max_pages=100,accept=DEFAULT_ACCEPT):
    fetcher=fetcher or fetch
    store=store if store is not None else GraphStore()
    if type(max_pages) is not int or max_pages<1:raise ValueError('分页预算无效')
    if not scope.allows(entry):raise ValueError('入口不在授权范围')
    queue=[entry];seen=set();pages=[];gaps=[]
    while queue and len(pages)<max_pages:
        url=queue.pop(0)
        if url in seen:continue
        seen.add(url)
        page={'request_url':url}
        pages.append(page)
        try:response=fetcher(url,source_id,headers={'Accept':accept})
        except Exception as exc:
            page['result']='fetch-error';page['error']=str(exc)
            gaps.append({'page':url,'result':'fetch-error','detail':str(exc)})
            continue
        page['effective_url']=response.url
        page['status']=response.status
        page['sha256']=response.sha256
        for name in ('content-type','content-location','vary'):
            value=response.header(name)
            if value is not None:page[name.replace('-','_')]=value
        if not 200<=response.status<300:
            result='redirect-not-followed' if 300<=response.status<400 else 'http-error'
            page['result']=result
            location=response.header('location')
            if location is not None:page['location']=location
            gaps.append({'page':url,'result':result,'detail':f'HTTP {response.status}'})
            continue
        base=urljoin(response.url,page['content_location']) if 'content_location' in page else response.url
        page['base_iri']=base
        scope_id=document_scope(source_id,response.url,response.sha256)
        try:outcome=parse_response(page.get('content_type'),response.body,base=base,scope=scope_id)
        except UnsupportedMedia as exc:
            page['result']='unsupported-media';page['error']=str(exc)
            gaps.append({'page':url,'result':'unsupported-media','detail':str(exc)})
            continue
        except ParseError as exc:
            page['result']='parse-error';page['error']=str(exc)
            gaps.append({'page':url,'result':'parse-error','detail':str(exc)})
            continue
        store.add_document(
            source_id=source_id,page_url=response.url,request_url=url,sha256=response.sha256,
            media=outcome.media,content_type=page.get('content_type'),
            content_location=page.get('content_location'),base_iri=base,
            declared_base=outcome.declared_base,quads=outcome.quads)
        page['result']='ok'
        page['document_scope']=scope_id
        page['assertions']=len(outcome.quads)
        if outcome.declared_base is not None:page['declared_base']=outcome.declared_base
        followed=[];outside=[]
        for target in next_links(response.headers):
            resolved=urljoin(response.url,target)
            if scope.allows(resolved):
                if resolved not in seen and resolved not in queue:queue.append(resolved)
                followed.append(resolved)
            else:outside.append(resolved)
        if followed:page['next']=followed
        if outside:page['out_of_scope']=outside
    if queue:
        gaps.append({'page':None,'result':'budget-exhausted','detail':f'分页预算耗尽，剩余{len(queue)}个未抓取','pending':list(queue)})
    return _report(entry,source_id,scope,accept,store,pages,gaps)

def _term_key(term):return json.dumps(term_json(term),ensure_ascii=False,sort_keys=True)

def _catalog(view):
    props={}
    edges=[]
    for quad,scopes in view.items():
        props.setdefault(quad.subject,{}).setdefault(quad.predicate,[]).append((quad.object,scopes))
        if quad.predicate==P_DISTRIBUTION:edges.append((quad.subject,quad.object,scopes))
    dataset_terms=set()
    for term,ps in props.items():
        if any(obj==T_DATASET for obj,_ in ps.get(P_TYPE,[])):dataset_terms.add(term)
    referrers={}
    for subject,dist,_ in edges:
        dataset_terms.add(subject)
        referrers.setdefault(dist,set()).add(subject)
    def types_of(term):
        return sorted({obj.value for obj,_ in props.get(term,{}).get(P_TYPE,[]) if isinstance(obj,Iri)})
    def titles_of(term):
        return [term_json(obj) for obj,_ in props.get(term,{}).get(P_TITLE,[]) if isinstance(obj,Literal)]
    def subject_evidence(term):
        return {scope for entries in props.get(term,{}).values() for _,scopes in entries for scope in scopes}
    datasets=[]
    for term in sorted(dataset_terms,key=_term_key):
        dists=[];edge_evidence=set()
        for subject,dist,scopes in edges:
            if subject==term:
                if dist not in dists:dists.append(dist)
                edge_evidence|=scopes
        datasets.append({
            'subject':term_json(term),
            'declared':any(obj==T_DATASET for obj,_ in props.get(term,{}).get(P_TYPE,[])),
            'types':types_of(term),
            'titles':titles_of(term),
            'distributions':[term_json(dist) for dist in dists],
            'evidence':sorted(subject_evidence(term)|edge_evidence),
        })
    distributions=[]
    for term in sorted(referrers,key=_term_key):
        ps=props.get(term,{})
        access=[term_json(obj) for obj,_ in ps.get(P_ACCESS,[])]
        download=[term_json(obj) for obj,_ in ps.get(P_DOWNLOAD,[])]
        license_=[term_json(obj) for obj,_ in ps.get(P_LICENSE,[])]
        edge_evidence={scope for _,dist,scopes in edges if dist==term for scope in scopes}
        distributions.append({
            'term':term_json(term),
            'described':term in props,
            'types':types_of(term),
            'titles':titles_of(term),
            'access_url':access,
            'download_url':download,
            'license':license_,
            'access_status':'declared' if access or download else 'missing',
            'license_status':'declared' if license_ else 'missing',
            'shared_by':[term_json(s) for s in sorted(referrers[term],key=_term_key)],
            'evidence':sorted(subject_evidence(term)|edge_evidence),
        })
    open_refs={}
    for role,pred in (('distribution',P_DISTRIBUTION),('license',P_LICENSE),('access_url',P_ACCESS),('download_url',P_DOWNLOAD)):
        for quad in view:
            if quad.predicate!=pred:continue
            term=quad.object
            if term in props:continue
            key=(role,_term_key(term))
            entry=open_refs.setdefault(key,{'role':role,'term':term_json(term),'referenced_by':[]})
            referrer=term_json(quad.subject)
            if referrer not in entry['referenced_by']:entry['referenced_by'].append(referrer)
    same_as=sorted(
        ({'subject':term_json(quad.subject),'object':term_json(quad.object)} for quad in view if quad.predicate==OWL_SAMEAS),
        key=lambda item:json.dumps(item,ensure_ascii=False,sort_keys=True))
    return datasets,distributions,[open_refs[key] for key in sorted(open_refs)],same_as

def _report(entry,source_id,scope,accept,store,pages,gaps):
    view=store.current_view()
    datasets,distributions,open_refs,same_as=_catalog(view)
    live=set(store.current.values())
    documents=[{**doc,'current':doc['scope'] in live} for doc in sorted(store.documents.values(),key=lambda doc:doc['seq'])]
    return {
        'source_id':source_id,
        'entry':entry,
        'scope':{'origin':scope.origin,'path_prefix':scope.path_prefix},
        'negotiation':{'accept':accept},
        'complete':not gaps,
        'gaps':gaps,
        'pages':pages,
        'assertions':len(view),
        'datasets':datasets,
        'distributions':distributions,
        'open_references':open_refs,
        'same_as':same_as,
        'documents':documents,
    }

def main():
    if len(sys.argv)!=2:raise SystemExit('请指定采集任务JSON路径')
    job=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    scope=Scope(job['scope']['origin'],job['scope'].get('path_prefix','/'))
    report=harvest(
        job['entry'],job['source_id'],scope,
        max_pages=job.get('max_pages',100),accept=job.get('accept',DEFAULT_ACCEPT))
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
