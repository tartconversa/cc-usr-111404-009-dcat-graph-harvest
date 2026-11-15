"""核对授权目录采集：分页、范围、项身份、空白节点作用域、状态与增量复用。"""
import json
import unittest
from pathlib import Path
from app.catalog import Scope,harvest,next_links
from app.lab import SourceServer
from app.rdf import RDF_TYPE,BNode,Iri,Literal,Quad,parse_nquads,parse_response,parse_turtle
from app.source import Response
from app.store import GraphStore,document_scope

SAMPLE=json.loads((Path(__file__).resolve().parents[1]/'fixtures/sample.json').read_text(encoding='utf-8'))
DCAT='http://www.w3.org/ns/dcat#'
DCT='http://purl.org/dc/terms/'
OWL='http://www.w3.org/2002/07/owl#'
CAT='https://cat.example'
SCOPE=Scope(CAT,'/catalog/')
PAGE1=CAT+'/catalog/page1'
PAGE2=CAT+'/catalog/page2'

PAGE1_TTL=f'''@prefix dcat: <{DCAT}> .
@prefix dct: <{DCT}> .
@prefix owl: <{OWL}> .

<dataset/a> a dcat:Dataset ;
    dct:title "农业气象观测"@zh , "Agri-weather observations"@en ;
    dcat:distribution <dist/a-csv> , _:d ;
    owl:sameAs <https://other.example/dataset/a> .

_:d dcat:downloadURL <files/a.csv> .

<dist/a-csv> a dcat:Distribution ;
    dct:title "CSV 分发"@zh ;
    dcat:accessURL <https://cat.example/catalog/api/a> ;
    dcat:downloadURL <files/a.csv> ;
    dct:license <https://cat.example/catalog/lic/cc-by> .
'''

PAGE2_TTL=f'''@prefix dcat: <{DCAT}> .
@prefix dct: <{DCT}> .

<dataset/b> a dcat:Dataset ;
    dct:title "土壤样本库"@zh ;
    dcat:distribution <dist/a-csv> , _:d , _:e .

_:d dct:title "第二页空白分发"@zh .
_:d dcat:accessURL <https://cat.example/catalog/api/b> .
'''

def make_response(url,body,*,source_id='src',status=200,headers=None):
    if isinstance(body,str):body=body.encode('utf-8')
    if headers is None:headers=(('Content-Type','text/turtle'),)
    return Response(source_id,url,status,tuple(headers),body)

class Stub:
    def __init__(self):self.pages={};self.calls=[]
    def add(self,url,response):self.pages[url]=response
    def fail(self,url,exc):self.pages[url]=exc
    def __call__(self,url,source_id,*,method='GET',headers=None,body=None,max_bytes=1048576):
        self.calls.append({'url':url,'source_id':source_id,'headers':dict(headers or {})})
        target=self.pages[url]
        if isinstance(target,Exception):raise target
        return target

def two_page_stub():
    stub=Stub()
    stub.add(PAGE1,make_response(PAGE1,PAGE1_TTL,headers=(
        ('Content-Type','text/turtle'),
        ('Link','</catalog/page2>; rel="next"'),
    )))
    stub.add(PAGE2,make_response(PAGE2,PAGE2_TTL,headers=(
        ('Content-Type','text/turtle'),
        ('Link','<https://other.example/catalog/page3>; rel="next"'),
    )))
    return stub

def iri_values(terms):return sorted(t['value'] for t in terms if t['kind']=='iri')

class ParserTests(unittest.TestCase):
    def test_turtle_features(self):
        text='''@prefix ex: <http://ex.test/> .
@base <http://ex.test/base/> .
# 注释
ex:s ex:p "chat"@FR , "typed"^^ex:dt , 42 , 3.14 , 1e2 , true ;
     ex:q [ ex:r ( "a" "b" ) ] ;
     ex:multi """line1
line2""" .
<> ex:self <../rel> .
'''
        quads,declared=parse_turtle(text,base='http://ex.test/doc',scope='s0')
        self.assertEqual(declared,'http://ex.test/base/')
        by_pred={}
        for quad in quads:by_pred.setdefault(quad.predicate.value,[]).append(quad)
        objects=by_pred['http://ex.test/p']
        self.assertIn(Literal('chat',lang='fr'),[q.object for q in objects])
        self.assertIn(Literal('typed','http://ex.test/dt'),[q.object for q in objects])
        self.assertIn(Literal('42','http://www.w3.org/2001/XMLSchema#integer'),[q.object for q in objects])
        self.assertIn(Literal('3.14','http://www.w3.org/2001/XMLSchema#decimal'),[q.object for q in objects])
        self.assertIn(Literal('1e2','http://www.w3.org/2001/XMLSchema#double'),[q.object for q in objects])
        self.assertIn(Literal('true','http://www.w3.org/2001/XMLSchema#boolean'),[q.object for q in objects])
        self.assertIn(Literal('line1\nline2'),[q.object for q in by_pred['http://ex.test/multi']])
        prop_list=[q.object for q in by_pred['http://ex.test/q']][0]
        self.assertIsInstance(prop_list,BNode)
        self.assertEqual(prop_list.scope,'s0')
        head=[q.object for q in quads if q.subject==prop_list and q.predicate==Iri('http://ex.test/r')][0]
        first=[q.object for q in quads if q.subject==head and q.predicate==Iri(RDF_TYPE.replace('type','first'))]
        self.assertEqual(first,[Literal('a')])
        rest=[q.object for q in quads if q.subject==head and q.predicate==Iri(RDF_TYPE.replace('type','rest'))][0]
        tail=[q.object for q in quads if q.subject==rest and q.predicate==Iri(RDF_TYPE.replace('type','rest'))]
        self.assertEqual(tail,[Iri(RDF_TYPE.replace('type','nil'))])
        self_quad=[q for q in by_pred['http://ex.test/self']][0]
        self.assertEqual(self_quad.subject,Iri('http://ex.test/base/'))
        self.assertEqual(self_quad.object,Iri('http://ex.test/rel'))

    def test_turtle_sparql_directives_and_escapes(self):
        text='PREFIX ex: <http://ex.test/>\nBASE <http://ex.test/b/>\nex:s ex:p "a\\tb" , "é" .\n'
        quads,declared=parse_turtle(text,base='http://ex.test/doc',scope='s')
        self.assertEqual(declared,'http://ex.test/b/')
        objects=[q.object for q in quads]
        self.assertIn(Literal('a\tb'),objects)
        self.assertIn(Literal('é'),objects)

    def test_nquads_keeps_graph_and_literal_identity(self):
        text='''<urn:s> <urn:p> <urn:o> <urn:g> .
<urn:s> <urn:p> <urn:o2> .
<urn:s> <urn:p> "lit"@EN .
<urn:s> <urn:p> "typed"^^<urn:dt> .
_:b <urn:p> <urn:o> <urn:g> .
'''
        quads=parse_nquads(text,scope='doc1')
        self.assertEqual(len(quads),5)
        self.assertIn(Quad(Iri('urn:s'),Iri('urn:p'),Iri('urn:o'),Iri('urn:g')),quads)
        self.assertIn(Quad(Iri('urn:s'),Iri('urn:p'),Iri('urn:o2'),None),quads)
        self.assertIn(Quad(Iri('urn:s'),Iri('urn:p'),Literal('lit',lang='en'),None),quads)
        self.assertIn(Quad(Iri('urn:s'),Iri('urn:p'),Literal('typed','urn:dt'),None),quads)
        self.assertIn(Quad(BNode('doc1','b'),Iri('urn:p'),Iri('urn:o'),Iri('urn:g')),quads)

    def test_parse_response_media_dispatch(self):
        outcome=parse_response('text/turtle; charset=utf-8',b'<a> <b> <c> .',base='http://x.test/',scope='s')
        self.assertEqual(outcome.media,'text/turtle')
        self.assertEqual(len(outcome.quads),1)
        outcome=parse_response('application/n-quads','<a> <b> <c> .'.encode(),base='http://x.test/',scope='s')
        self.assertEqual(len(outcome.quads),1)
        with self.assertRaises(Exception):parse_response('text/html',b'<html>',base='http://x.test/',scope='s')

class HarvestTests(unittest.TestCase):
    def test_pagination_scope_and_no_stray_fetch(self):
        stub=two_page_stub()
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        self.assertTrue(report['complete'])
        self.assertEqual([call['url'] for call in stub.calls],[PAGE1,PAGE2])
        page2=[p for p in report['pages'] if p['request_url']==PAGE2][0]
        self.assertEqual(page2['out_of_scope'],['https://other.example/catalog/page3'])
        self.assertEqual(len(report['datasets']),2)
        titles={t['lexical'] for d in report['datasets'] for t in d['titles']}
        self.assertEqual(titles,{'农业气象观测','Agri-weather observations','土壤样本库'})

    def test_accept_header_and_representation_recorded(self):
        stub=two_page_stub()
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        self.assertIn('text/turtle',stub.calls[0]['headers']['Accept'])
        page1=report['pages'][0]
        self.assertEqual(page1['content_type'],'text/turtle')
        self.assertEqual(page1['base_iri'],PAGE1)
        self.assertEqual(report['negotiation']['accept'],report['negotiation']['accept'])

    def test_license_not_treated_as_download_and_statuses(self):
        report=harvest(PAGE1,'src',SCOPE,fetcher=two_page_stub())
        dists={d['term']['value']:d for d in report['distributions'] if d['term']['kind']=='iri'}
        shared=dists[CAT+'/catalog/dist/a-csv']
        self.assertEqual(iri_values(shared['license']),[CAT+'/catalog/lic/cc-by'])
        self.assertEqual(iri_values(shared['access_url']),[CAT+'/catalog/api/a'])
        self.assertEqual(iri_values(shared['download_url']),[CAT+'/catalog/files/a.csv'])
        self.assertEqual(shared['license_status'],'declared')
        self.assertEqual(shared['access_status'],'declared')
        bnodes=[d for d in report['distributions'] if d['term']['kind']=='bnode']
        described=[d for d in bnodes if d['described']]
        self.assertEqual(len(described),2)
        for dist in described:
            self.assertEqual(dist['license_status'],'missing')
            self.assertEqual(dist['access_status'],'declared')
        undescribed=[d for d in bnodes if not d['described']]
        self.assertEqual(len(undescribed),1)
        self.assertEqual(undescribed[0]['access_status'],'missing')
        self.assertEqual(undescribed[0]['license_status'],'missing')

    def test_shared_distribution_and_bnode_no_cross_page_merge(self):
        report=harvest(PAGE1,'src',SCOPE,fetcher=two_page_stub())
        dists={d['term']['value']:d for d in report['distributions'] if d['term']['kind']=='iri'}
        shared=dists[CAT+'/catalog/dist/a-csv']
        self.assertEqual(len(shared['shared_by']),2)
        bnodes=[d for d in report['distributions'] if d['term']['kind']=='bnode' and d['term']['label']=='d']
        self.assertEqual(len(bnodes),2)
        scopes={d['term']['scope'] for d in bnodes}
        self.assertEqual(len(scopes),2)
        by_scope={d['term']['scope']:d for d in bnodes}
        access_sets={s:iri_values(d['access_url']) for s,d in by_scope.items()}
        self.assertEqual(sorted(access_sets.values()),[[],[CAT+'/catalog/api/b']])

    def test_sameas_recorded_not_merged(self):
        report=harvest(PAGE1,'src',SCOPE,fetcher=two_page_stub())
        self.assertEqual(len(report['same_as']),1)
        link=report['same_as'][0]
        self.assertEqual(link['object']['value'],'https://other.example/dataset/a')
        subjects={d['subject']['value'] for d in report['datasets']}
        self.assertNotIn('https://other.example/dataset/a',subjects)

    def test_open_references_and_literal_identity(self):
        report=harvest(PAGE1,'src',SCOPE,fetcher=two_page_stub())
        roles={(r['role'],r['term']['value']) for r in report['open_references'] if r['term']['kind']=='iri'}
        self.assertIn(('license',CAT+'/catalog/lic/cc-by'),roles)
        self.assertIn(('download_url',CAT+'/catalog/files/a.csv'),roles)
        self.assertIn(('access_url',CAT+'/catalog/api/a'),roles)
        bnode_refs=[r for r in report['open_references'] if r['term']['kind']=='bnode']
        self.assertEqual(len(bnode_refs),1)
        self.assertEqual(bnode_refs[0]['term']['label'],'e')
        dataset_a=[d for d in report['datasets'] if d['subject']['value']==CAT+'/catalog/dataset/a'][0]
        langs={t.get('lang') for t in dataset_a['titles']}
        self.assertEqual(langs,{'zh','en'})

    def test_partial_failure_delivers_partial_catalog(self):
        stub=two_page_stub()
        stub.add(PAGE2,make_response(PAGE2,'server error',status=500,headers=(('Content-Type','text/plain'),)))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        self.assertFalse(report['complete'])
        self.assertEqual([g['result'] for g in report['gaps']],['http-error'])
        subjects={d['subject']['value'] for d in report['datasets']}
        self.assertIn(CAT+'/catalog/dataset/a',subjects)
        self.assertGreater(report['assertions'],0)

    def test_fetch_error_and_budget_gap(self):
        stub=Stub()
        stub.fail(PAGE1,OSError('连接被拒'))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        self.assertFalse(report['complete'])
        self.assertEqual(report['gaps'][0]['result'],'fetch-error')
        self.assertEqual(report['datasets'],[])
        stub=two_page_stub()
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub,max_pages=1)
        self.assertFalse(report['complete'])
        self.assertEqual(report['gaps'][0]['result'],'budget-exhausted')

    def test_redirect_not_followed_and_unsupported_media(self):
        stub=Stub()
        stub.add(PAGE1,make_response(PAGE1,'',status=302,headers=(('Location','/catalog/elsewhere'),)))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        self.assertEqual(report['pages'][0]['result'],'redirect-not-followed')
        self.assertEqual(report['pages'][0]['location'],'/catalog/elsewhere')
        self.assertEqual(len(stub.calls),1)
        stub=Stub()
        stub.add(PAGE1,make_response(PAGE1,'<html></html>',headers=(('Content-Type','text/html'),)))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        self.assertEqual(report['pages'][0]['result'],'unsupported-media')
        self.assertFalse(report['complete'])

    def test_entry_out_of_scope_rejected(self):
        with self.assertRaises(ValueError):harvest('https://other.example/catalog/','src',SCOPE,fetcher=Stub())
        with self.assertRaises(ValueError):harvest(CAT+'/other/page','src',SCOPE,fetcher=Stub())

    def test_content_location_and_declared_base(self):
        stub=Stub()
        stub.add(PAGE1,make_response(PAGE1,'<ds> <p> <o> .',headers=(
            ('Content-Type','text/turtle'),('Content-Location','/catalog/canonical'))))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        page=report['pages'][0]
        self.assertEqual(page['base_iri'],CAT+'/catalog/canonical')
        stub=Stub()
        stub.add(PAGE1,make_response(PAGE1,f'@base <{CAT}/deep/> .\n<ds> a <{DCAT}Dataset> .'))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub)
        page=report['pages'][0]
        self.assertEqual(page['declared_base'],CAT+'/deep/')
        self.assertEqual(report['datasets'][0]['subject']['value'],CAT+'/deep/ds')

    def test_retry_same_response_adds_nothing(self):
        stub=two_page_stub()
        store=GraphStore()
        first=harvest(PAGE1,'src',SCOPE,fetcher=stub,store=store)
        size=len(store.assertions)
        second=harvest(PAGE1,'src',SCOPE,fetcher=stub,store=store)
        self.assertEqual(len(store.assertions),size)
        self.assertEqual(len(store.documents),2)
        self.assertEqual(first['assertions'],second['assertions'])
        bnodes={q.subject for q in store.current_view() if isinstance(q.subject,BNode)}
        self.assertEqual(len(bnodes),len({(b.scope,b.label) for b in bnodes}))

    def test_incremental_reuse_after_page_change(self):
        stub=two_page_stub()
        store=GraphStore()
        harvest(PAGE1,'src',SCOPE,fetcher=stub,store=store)
        changed=PAGE2_TTL.replace('土壤样本库','土壤样本库v2')
        stub.add(PAGE2,make_response(PAGE2,changed))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub,store=store)
        self.assertEqual(len(store.documents),3)
        dataset_b=[d for d in report['datasets'] if d['subject']['value']==CAT+'/catalog/dataset/b'][0]
        self.assertEqual([t['lexical'] for t in dataset_b['titles']],['土壤样本库v2'])
        docs={doc['sha256']:doc for doc in report['documents']}
        currents=[doc for doc in report['documents'] if doc['page_url']==PAGE2]
        self.assertEqual(sum(1 for doc in currents if doc['current']),1)
        self.assertEqual(dataset_b['evidence'],[doc['scope'] for doc in currents if doc['current']])

    def test_failed_refetch_keeps_previous_page_data(self):
        stub=two_page_stub()
        store=GraphStore()
        harvest(PAGE1,'src',SCOPE,fetcher=stub,store=store)
        stub.add(PAGE2,make_response(PAGE2,'down',status=503,headers=(('Content-Type','text/plain'),)))
        report=harvest(PAGE1,'src',SCOPE,fetcher=stub,store=store)
        self.assertFalse(report['complete'])
        subjects={d['subject']['value'] for d in report['datasets']}
        self.assertIn(CAT+'/catalog/dataset/b',subjects)

class LinkHeaderTests(unittest.TestCase):
    def test_next_link_parsing(self):
        headers=(('Link','</a>; rel="next", </b>; rel="canonical"'),
                 ('link','</c>; rel="prev next"; type="text/turtle"'),)
        self.assertEqual(next_links(headers),['/a','/c'])
        self.assertEqual(next_links((('Link','</x,y>; rel="next"'),)),['/x,y'])

class LabIntegrationTests(unittest.TestCase):
    def test_harvest_against_lab_server(self):
        with SourceServer(SAMPLE) as server:
            origin=server.url.rsplit('/source',1)[0]
            report=harvest(server.url,SAMPLE['source_id'],Scope(origin,'/'))
        self.assertTrue(report['complete'])
        self.assertEqual(len(report['datasets']),1)
        dataset=report['datasets'][0]
        self.assertEqual(dataset['subject']['value'],'urn:data:a')
        self.assertFalse(dataset['declared'])
        dist=report['distributions'][0]
        self.assertEqual(dist['term']['kind'],'bnode')
        self.assertEqual(iri_values(dist['download_url']),['https://example.test/data.csv'])
        self.assertEqual(dist['access_status'],'declared')
        self.assertEqual(dist['license_status'],'missing')
        self.assertEqual(len(report['same_as']),0)

if __name__=='__main__':unittest.main()
