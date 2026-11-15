"""RDF项模型与Turtle/N-Quads解析；仅使用标准库。

项身份规则：IRI按解析结果原样保留；空白节点绑定一次解析文档的作用域，
不同文档不凭标签合并；字面量保留词形、语言标记与数据类型，显示文本不作为身份。
语言标记规范化为小写，朴素字面量按RDF 1.1记为xsd:string，带语言字面量记为
rdf:langString。默认图以None表示，与命名图不混同。"""
import re
from dataclasses import dataclass
from urllib.parse import urljoin,urlsplit

RDF='http://www.w3.org/1999/02/22-rdf-syntax-ns#'
XSD='http://www.w3.org/2001/XMLSchema#'
RDF_TYPE=RDF+'type'
RDF_FIRST=RDF+'first'
RDF_REST=RDF+'rest'
RDF_NIL=RDF+'nil'
LANG_STRING=RDF+'langString'
XSD_STRING=XSD+'string'
XSD_BOOLEAN=XSD+'boolean'
XSD_INTEGER=XSD+'integer'
XSD_DECIMAL=XSD+'decimal'
XSD_DOUBLE=XSD+'double'

class ParseError(ValueError):pass
class UnsupportedMedia(ValueError):pass

@dataclass(frozen=True)
class Iri:
    value:str

@dataclass(frozen=True)
class BNode:
    scope:str
    label:str

@dataclass(frozen=True)
class Literal:
    lexical:str
    datatype:str=XSD_STRING
    lang:str=None
    def __post_init__(self):
        if self.lang is not None:
            object.__setattr__(self,'lang',self.lang.lower())
            object.__setattr__(self,'datatype',LANG_STRING)

@dataclass(frozen=True)
class Quad:
    subject:object
    predicate:object
    object:object
    graph:object=None

@dataclass(frozen=True)
class ParseOutcome:
    media:str
    quads:tuple
    declared_base:str=None

def term_json(term):
    if isinstance(term,Iri):return {'kind':'iri','value':term.value}
    if isinstance(term,BNode):return {'kind':'bnode','scope':term.scope,'label':term.label}
    if isinstance(term,Literal):
        out={'kind':'literal','lexical':term.lexical,'datatype':term.datatype}
        if term.lang is not None:out['lang']=term.lang
        return out
    raise TypeError('未知RDF项')

_HEX=set('0123456789abcdefABCDEF')
_LANG=re.compile(r'[a-zA-Z]+(?:-[a-zA-Z0-9]+)*')
_NUMBER=re.compile(r'[+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)[eE][+-]?\d+|\d+\.\d*|\.\d+|\d+)')
_SIMPLE_ESCAPES={'t':'\t','b':'\b','n':'\n','r':'\r','f':'\f','"':'"',"'":"'",'\\':'\\'}

class _Reader:
    def __init__(self,text):self.text=text;self.n=len(text);self.pos=0
    def error(self,msg):raise ParseError(f'{msg}（偏移{self.pos}）')
    def peek(self):return self.text[self.pos] if self.pos<self.n else ''
    def skip_ws(self):
        while self.pos<self.n:
            c=self.text[self.pos]
            if c in ' \t\r\n':self.pos+=1
            elif c=='#':
                while self.pos<self.n and self.text[self.pos] not in '\r\n':self.pos+=1
            else:break
    def expect(self,char):
        if self.peek()!=char:self.error(f'缺少{char!r}')
        self.pos+=1
    def _unicode_escape(self):
        kind=self.text[self.pos]
        width=4 if kind=='u' else 8
        digits=self.text[self.pos+1:self.pos+1+width]
        if len(digits)!=width or not all(ch in _HEX for ch in digits):self.error('Unicode转义无效')
        self.pos+=1+width
        return chr(int(digits,16))
    def _char_or_escape(self):
        c=self.text[self.pos]
        if c!='\\':self.pos+=1;return c
        self.pos+=1
        if self.pos>=self.n:self.error('转义未完成')
        e=self.text[self.pos]
        if e in _SIMPLE_ESCAPES:self.pos+=1;return _SIMPLE_ESCAPES[e]
        if e in 'uU':return self._unicode_escape()
        self.error('未知转义')
    def read_iriref_token(self):
        self.expect('<')
        out=[]
        while True:
            if self.pos>=self.n:self.error('IRI未闭合')
            c=self.text[self.pos]
            if c=='>':self.pos+=1;return ''.join(out)
            if c=='\\':
                self.pos+=1
                if self.pos>=self.n or self.text[self.pos] not in 'uU':self.error('IRI内转义无效')
                out.append(self._unicode_escape());continue
            if ord(c)<=0x20 or c in '<>"{}|^`':self.error('IRI含非法字符')
            out.append(c);self.pos+=1
    def read_string_token(self):
        quote=self.peek()
        if quote not in ('"',"'"):self.error('缺少字符串引号')
        if self.text[self.pos:self.pos+3]==quote*3:
            self.pos+=3;out=[]
            while True:
                if self.pos>=self.n:self.error('长字符串未闭合')
                if self.text[self.pos:self.pos+3]==quote*3:self.pos+=3;return ''.join(out)
                out.append(self._char_or_escape())
        self.pos+=1;out=[]
        while True:
            if self.pos>=self.n:self.error('字符串未闭合')
            c=self.text[self.pos]
            if c==quote:self.pos+=1;return ''.join(out)
            if c in '\r\n':self.error('字符串跨行')
            out.append(self._char_or_escape())

class _Turtle(_Reader):
    def __init__(self,text,*,base,scope):
        super().__init__(text)
        if base is not None and not urlsplit(base).scheme:raise ParseError('文档基址非绝对IRI')
        self.base=base
        self.declared_base=None
        self.prefixes={}
        self.scope=scope
        self.gen=0
        self.used_labels=set()
        self.quads=[]
    def resolve(self,ref):
        if urlsplit(ref).scheme:return ref
        if self.base is None:self.error(f'相对IRI缺少基址：{ref}')
        return urljoin(self.base,ref)
    def parse(self):
        self.skip_ws()
        while self.pos<self.n:
            if self.peek()=='@':self._directive()
            elif self._keyword('PREFIX'):self._prefix_decl()
            elif self._keyword('BASE'):self._base_decl()
            else:self._triples()
            self.skip_ws()
        return self.quads
    def _keyword(self,word):
        end=self.pos+len(word)
        if self.text[self.pos:end].upper()==word and (end>=self.n or self.text[end] in ' \t\r\n'):
            self.pos=end
            return True
        return False
    def _require_ws(self):
        if self.pos<self.n and self.text[self.pos] not in ' \t\r\n':self.error('指令后缺少空白')
        self.skip_ws()
    def _directive(self):
        self.pos+=1
        start=self.pos
        while self.pos<self.n and self.text[self.pos].isalpha():self.pos+=1
        word=self.text[start:self.pos]
        if word=='prefix':self._prefix_decl()
        elif word=='base':self._base_decl()
        else:self.error(f'未知指令@{word}')
        self.skip_ws()
        self.expect('.')
    def _prefix_decl(self):
        self._require_ws()
        name=self._read_prefix_name()
        self.skip_ws()
        self.prefixes[name]=self.resolve(self.read_iriref_token())
    def _base_decl(self):
        self._require_ws()
        self.base=self.resolve(self.read_iriref_token())
        self.declared_base=self.base
    def _read_prefix_name(self):
        start=self.pos
        while self.pos<self.n and (self.text[self.pos].isalnum() or self.text[self.pos] in '_.-'):self.pos+=1
        name=self.text[start:self.pos]
        if self.peek()!=':':self.error('前缀名缺少冒号')
        self.pos+=1
        if name.endswith('.'):self.error('前缀名以句点结尾')
        return name
    def _triples(self):
        subject,maybe_empty=self._subject()
        self.skip_ws()
        if self.peek()=='.':
            if not maybe_empty:self.error('缺少谓词')
            self.pos+=1
            return
        self._predicate_object_list(subject)
        self.skip_ws()
        self.expect('.')
    def _subject(self):
        c=self.peek()
        if c=='<':return Iri(self.resolve(self.read_iriref_token())),False
        if c=='[':return self._blank_property_list(),True
        if c=='(':return self._collection(),True
        if self.text[self.pos:self.pos+2]=='_:':return self._bnode_label(),False
        return Iri(self._prefixed()),False
    def _predicate_object_list(self,subject):
        while True:
            self.skip_ws()
            verb=self._verb()
            self._object_list(subject,verb)
            self.skip_ws()
            if self.peek()!=';':return
            self.pos+=1
            self.skip_ws()
            if self.peek() in '.]':return
    def _verb(self):
        if self.peek()=='a' and (self.pos+1>=self.n or self.text[self.pos+1] in ' \t\r\n'):
            self.pos+=1
            return Iri(RDF_TYPE)
        return Iri(self._iri())
    def _iri(self):
        if self.peek()=='<':return self.resolve(self.read_iriref_token())
        return self._prefixed()
    def _prefixed(self):
        name=self._read_prefix_name()
        if name not in self.prefixes:self.error(f'未声明前缀：{name}')
        return self.prefixes[name]+self._read_local()
    def _read_local(self):
        out=[]
        while self.pos<self.n:
            c=self.text[self.pos]
            if c=='\\':
                nxt=self.text[self.pos+1] if self.pos+1<self.n else ''
                if nxt in "_~.-!$&'()*+,;=/?#@%":out.append(nxt);self.pos+=2;continue
                self.error('前缀名转义无效')
            if c.isalnum() or c in '_-:%.~':out.append(c);self.pos+=1;continue
            break
        local=''.join(out)
        while local.endswith('.'):
            local=local[:-1]
            self.pos-=1
        return local
    def _object_list(self,subject,predicate):
        while True:
            self.skip_ws()
            obj=self._object()
            self.quads.append(Quad(subject,predicate,obj,None))
            self.skip_ws()
            if self.peek()!=',':return
            self.pos+=1
    def _object(self):
        c=self.peek()
        if c=='<':return Iri(self.resolve(self.read_iriref_token()))
        if c=='[':return self._blank_property_list()
        if c=='(':return self._collection()
        if self.text[self.pos:self.pos+2]=='_:':return self._bnode_label()
        if c in ('"',"'"):return self._string_literal()
        if c.isdigit() or c in '+-.':return self._number()
        if self._boolean_keyword('true'):return Literal('true',XSD_BOOLEAN)
        if self._boolean_keyword('false'):return Literal('false',XSD_BOOLEAN)
        return Iri(self._prefixed())
    def _boolean_keyword(self,word):
        end=self.pos+len(word)
        if self.text[self.pos:end]==word and (end>=self.n or self.text[end] in ' \t\r\n;,.)]}'):
            self.pos=end
            return True
        return False
    def _string_literal(self):
        lexical=self.read_string_token()
        if self.peek()=='@':
            self.pos+=1
            match=_LANG.match(self.text,self.pos)
            if not match or match.start()!=self.pos:self.error('语言标记无效')
            self.pos=match.end()
            return Literal(lexical,lang=match.group())
        if self.text[self.pos:self.pos+2]=='^^':
            self.pos+=2
            return Literal(lexical,datatype=self._iri())
        return Literal(lexical)
    def _number(self):
        match=_NUMBER.match(self.text,self.pos)
        if not match:self.error('数值无效')
        token=match.group()
        self.pos=match.end()
        if self.pos<self.n and self.text[self.pos] not in ' \t\r\n;,)]}':self.error('数值后缺少定界')
        if 'e' in token or 'E' in token:datatype=XSD_DOUBLE
        elif '.' in token:datatype=XSD_DECIMAL
        else:datatype=XSD_INTEGER
        return Literal(token,datatype)
    def _bnode_label(self):
        self.pos+=2
        start=self.pos
        while self.pos<self.n and (self.text[self.pos].isalnum() or self.text[self.pos] in '_.-'):self.pos+=1
        label=self.text[start:self.pos]
        if not label:self.error('空白节点标签为空')
        self.used_labels.add(label)
        return BNode(self.scope,label)
    def _fresh(self):
        while True:
            self.gen+=1
            label=f'gen{self.gen}'
            if label not in self.used_labels:
                self.used_labels.add(label)
                return BNode(self.scope,label)
    def _blank_property_list(self):
        self.expect('[')
        node=self._fresh()
        self.skip_ws()
        if self.peek()==']':
            self.pos+=1
            return node
        self._predicate_object_list(node)
        self.skip_ws()
        self.expect(']')
        return node
    def _collection(self):
        self.expect('(')
        items=[]
        self.skip_ws()
        while self.peek()!=')':
            if self.pos>=self.n:self.error('集合未闭合')
            items.append(self._object())
            self.skip_ws()
        self.pos+=1
        if not items:return Iri(RDF_NIL)
        head=None
        prev=None
        for item in items:
            node=self._fresh()
            self.quads.append(Quad(node,Iri(RDF_FIRST),item,None))
            if prev is None:head=node
            else:self.quads.append(Quad(prev,Iri(RDF_REST),node,None))
            prev=node
        self.quads.append(Quad(prev,Iri(RDF_REST),Iri(RDF_NIL),None))
        return head

def parse_turtle(text,*,base,scope):
    parser=_Turtle(text,base=base,scope=scope)
    return parser.parse(),parser.declared_base

def _nq_bnode(reader,scope):
    reader.pos+=2
    start=reader.pos
    while reader.pos<reader.n and (reader.text[reader.pos].isalnum() or reader.text[reader.pos] in '_.-'):reader.pos+=1
    label=reader.text[start:reader.pos]
    if not label:reader.error('空白节点标签为空')
    return BNode(scope,label)

def _nq_term(reader,scope,allow_literal):
    c=reader.peek()
    if c=='<':return Iri(reader.read_iriref_token())
    if reader.text[reader.pos:reader.pos+2]=='_:':return _nq_bnode(reader,scope)
    if c=='"' and allow_literal:
        lexical=reader.read_string_token()
        if reader.text[reader.pos:reader.pos+2]=='^^':
            reader.pos+=2
            return Literal(lexical,datatype=reader.read_iriref_token())
        if reader.peek()=='@':
            reader.pos+=1
            match=_LANG.match(reader.text,reader.pos)
            if not match or match.start()!=reader.pos:reader.error('语言标记无效')
            reader.pos=match.end()
            return Literal(lexical,lang=match.group())
        return Literal(lexical)
    reader.error('项无效')

def parse_nquads(text,*,scope):
    quads=[]
    for lineno,line in enumerate(text.splitlines(),1):
        reader=_Reader(line)
        reader.skip_ws()
        if reader.pos>=reader.n or reader.peek()=='#':continue
        try:
            subject=_nq_term(reader,scope,False)
            reader.skip_ws()
            predicate=Iri(reader.read_iriref_token())
            reader.skip_ws()
            obj=_nq_term(reader,scope,True)
            reader.skip_ws()
            graph=None
            if reader.peek()!='.':
                graph=_nq_term(reader,scope,False)
                reader.skip_ws()
            reader.expect('.')
            reader.skip_ws()
            if reader.pos<reader.n and reader.peek()!='#':reader.error('句点后存在多余内容')
        except ParseError as exc:
            raise ParseError(f'第{lineno}行：{exc}') from exc
        quads.append(Quad(subject,predicate,obj,graph))
    return quads

TURTLE_MEDIA={'text/turtle','application/turtle','application/x-turtle'}
NQUADS_MEDIA={'application/n-quads'}
NTRIPLES_MEDIA={'application/n-triples'}

def parse_response(content_type,body,*,base,scope):
    media,_,param_text=(content_type or '').partition(';')
    media=media.strip().lower()
    params={}
    for part in param_text.split(';'):
        key,_,value=part.partition('=')
        if value:params[key.strip().lower()]=value.strip().strip('"')
    if media in TURTLE_MEDIA:
        charset=params.get('charset','utf-8')
        try:text=body.decode(charset)
        except (LookupError,UnicodeDecodeError) as exc:raise ParseError(f'字节解码失败：{exc}') from exc
        quads,declared_base=parse_turtle(text,base=base,scope=scope)
        return ParseOutcome(media,tuple(quads),declared_base)
    if media in NQUADS_MEDIA or media in NTRIPLES_MEDIA:
        try:text=body.decode('utf-8')
        except UnicodeDecodeError as exc:raise ParseError(f'字节解码失败：{exc}') from exc
        return ParseOutcome(media,tuple(parse_nquads(text,scope=scope)))
    raise UnsupportedMedia(f'未支持的Content-Type：{content_type!r}')
