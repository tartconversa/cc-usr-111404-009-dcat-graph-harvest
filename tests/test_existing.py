"""核对现有来源读取、响应概览与请求接口。"""
import base64,copy,json,unittest
from pathlib import Path
from app.source import from_packet,fetch
from app.service import inspect,collect_one
from app.lab import SourceServer

SAMPLE=json.loads((Path(__file__).resolve().parents[1]/'fixtures/sample.json').read_text(encoding='utf-8'))
EXPECTED={'nonempty_lines': 2, 'content_type': 'application/n-quads'}

class ExistingTests(unittest.TestCase):
    def test_existing_summary(self):
        result=inspect(SAMPLE)
        self.assertEqual(result['summary'],EXPECTED)
        self.assertEqual(result['source_id'],SAMPLE['source_id'])
        self.assertEqual(len(result['response_sha256']),64)

    def test_packet_header_case_and_repeats(self):
        data=copy.deepcopy(SAMPLE);data['headers']=[['X-Name','one'],['x-name','two']]
        response=from_packet(data)
        self.assertEqual(response.header('X-NAME'),'one, two')
        self.assertIsNone(response.header('absent'))

    def test_invalid_packet_rejected(self):
        for change in ({'status':True},{'body_base64':'!invalid!'},{'headers':[['A','bad\r\nInjected: x']]},{'source_id':''}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):from_packet({**SAMPLE,**change})

    def test_response_bytes_are_exact_and_isolated(self):
        data=copy.deepcopy(SAMPLE);data['body_base64']=base64.b64encode(b'\x00\xff\r\n').decode()
        response=from_packet(data);data['headers'].clear()
        self.assertEqual(response.body,b'\x00\xff\r\n')
        self.assertTrue(response.headers)

    def test_actual_http_collect_one(self):
        with SourceServer(SAMPLE) as server:
            result=collect_one(server.url,SAMPLE['source_id'])
            self.assertEqual(result,inspect(SAMPLE))
            self.assertEqual(server.requests[0]['path'],'/source')

    def test_request_body_and_method_preserved(self):
        with SourceServer(SAMPLE) as server:
            fetch(server.url,'local',method='POST',headers={'X-Scope':'test'},body=b'query')
            request=server.requests[0]
            self.assertEqual(request['method'],'POST')
            self.assertEqual(base64.b64decode(request['body_base64']),b'query')
            self.assertIn(['X-Scope','test'],[list(pair) for pair in request['headers']])

    def test_source_does_not_follow_redirect(self):
        packet={**SAMPLE,'status':302,'headers':[['Location','/must-not-fetch']],'body_base64':''}
        with SourceServer(packet) as server:
            response=fetch(server.url,'local')
            self.assertEqual(response.status,302)
            self.assertEqual(len(server.requests),1)

    def test_read_budget_and_error_response(self):
        with SourceServer(SAMPLE) as server:
            with self.assertRaisesRegex(ValueError,'预算'):fetch(server.url,'local',max_bytes=1)
            response=fetch(server.url+'/missing','local')
            self.assertEqual(response.status,404)

if __name__=='__main__':unittest.main()
