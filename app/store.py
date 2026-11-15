"""带来源的断言存储：文档作用域、当前视图与增量复用。

文档作用域绑定source_id、有效URL与原始响应摘要：同一响应重试得到同一作用域，
空白节点身份不变，不凭新造ID增加资料；不同响应即不同文档，空白节点不跨文档合并。
每条断言记录出自哪些文档作用域；当前视图按（来源,页面）取最新一次成功解析，
换页后旧作用域的断言退出视图但留档可查，不宣称目录齐全。"""
import hashlib

def document_scope(source_id,page_url,sha256):
    material=f'{source_id}\x00{page_url}\x00{sha256}'.encode('utf-8')
    return hashlib.sha256(material).hexdigest()

class GraphStore:
    def __init__(self):
        self.documents={}
        self.current={}
        self.assertions={}
        self.seq=0

    def add_document(self,*,source_id,page_url,request_url,sha256,media,content_type,content_location,base_iri,declared_base,quads):
        scope=document_scope(source_id,page_url,sha256)
        if scope not in self.documents:
            self.seq+=1
            self.documents[scope]={
                'scope':scope,'seq':self.seq,'source_id':source_id,'page_url':page_url,
                'request_url':request_url,'sha256':sha256,'media':media,
                'content_type':content_type,'content_location':content_location,
                'base_iri':base_iri,'declared_base':declared_base,'quad_count':len(quads),
            }
        for quad in quads:self.assertions.setdefault(quad,set()).add(scope)
        self.current[(source_id,page_url)]=scope
        return scope

    def current_view(self):
        live=set(self.current.values())
        return {quad:scopes&live for quad,scopes in self.assertions.items() if scopes&live}
