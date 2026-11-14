"""在回环地址提供可复现的来源响应，用于接口联调。"""
import base64
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class SourceServer:
    def __init__(self,packet):
        self.packet=copy.deepcopy(packet);self.requests=[];self.lock=threading.Lock()

    def __enter__(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def handle_request(self):
                size=int(self.headers.get('Content-Length','0'));body=self.rfile.read(size)
                with owner.lock:owner.requests.append({'method':self.command,'path':self.path,'headers':list(self.headers.items()),'body_base64':base64.b64encode(body).decode()})
                if self.path!='/source':
                    self.send_response(404);self.send_header('Content-Length','0');self.end_headers();return
                packet=owner.packet;data=base64.b64decode(packet['body_base64'])
                self.send_response(packet['status'])
                for key,value in packet['headers']:self.send_header(key,value)
                if not any(k.lower()=='content-length' for k,v in packet['headers']):self.send_header('Content-Length',str(len(data)))
                self.end_headers()
                if self.command!='HEAD':self.wfile.write(data)
            do_GET=do_POST=do_HEAD=do_PROPFIND=do_REPORT=handle_request
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.01},daemon=True)
        self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)+'/source'
        return self

    def __exit__(self,*args):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        if self.thread.is_alive():raise RuntimeError('来源服务未停止')

def main():
    import sys
    from pathlib import Path
    packet=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    with SourceServer(packet) as server:
        print(server.url,flush=True)
        try:threading.Event().wait()
        except KeyboardInterrupt:pass

if __name__=='__main__':main()
