"""原始来源响应及一次请求读取；不自动解释业务完整性。"""
import base64
import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass

@dataclass(frozen=True)
class Response:
    source_id: str
    url: str
    status: int
    headers: tuple
    body: bytes

    def header(self,name):
        return ', '.join(value for key,value in self.headers if key.lower()==name.lower()) or None

    @property
    def sha256(self):return hashlib.sha256(self.body).hexdigest()

def from_packet(packet):
    if not isinstance(packet.get('source_id'),str) or not packet['source_id']:raise ValueError('来源身份无效')
    status=packet.get('status')
    if type(status) is not int or not 100<=status<=599:raise ValueError('响应状态无效')
    headers=packet.get('headers')
    if not isinstance(headers,list):raise ValueError('响应头无效')
    pairs=[]
    for pair in headers:
        if not isinstance(pair,list) or len(pair)!=2 or not all(isinstance(v,str) for v in pair):raise ValueError('响应头结构无效')
        if not pair[0] or any(x in pair[0]+pair[1] for x in ('\r','\n')):raise ValueError('响应头注入')
        pairs.append(tuple(pair))
    try:body=base64.b64decode(packet['body_base64'],validate=True)
    except Exception as exc:raise ValueError('响应字节编码无效') from exc
    return Response(packet['source_id'],packet['url'],status,tuple(pairs),body)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None

def fetch(url,source_id,*,method='GET',headers=None,body=None,max_bytes=1048576):
    if type(max_bytes) is not int or max_bytes<0:raise ValueError('读取预算无效')
    request=urllib.request.Request(url,data=body,headers=headers or {},method=method)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:stream=opener.open(request,timeout=5)
    except urllib.error.HTTPError as exc:stream=exc
    with stream:
        data=stream.read(max_bytes+1)
        if len(data)>max_bytes:raise ValueError('响应超过读取预算')
        return Response(source_id,stream.geturl(),stream.code,tuple(stream.headers.raw_items()),data)
