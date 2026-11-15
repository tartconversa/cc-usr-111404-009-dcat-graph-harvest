# 开放科创资源DCAT图的空白节点与分发关系采集

现有组件读取一份来源响应，输出当前概览及字节摘要；采集组件在此之上按授权范围领取目录分页并归并数据集与分发关系。
app/source.py保留响应状态、重复头和实体原字节，一次请求不自动跟随重定向；app/baseline.py解释当前单响应，app/service.py将概览绑定到来源身份及摘要。
app/rdf.py解析Turtle与N-Quads，IRI、空白节点、带语言或类型的字面量各自保留身份；app/store.py按文档作用域存放带来源的断言并支持增量复用；app/catalog.py按入口、Link rel=next分页和origin+path前缀范围采集，只记录不抓取目录中的任意IRI。
app/lab.py提供回环HTTP来源，支持GET、POST、HEAD、PROPFIND和REPORT，记录收到的请求，用于在可控来源上联调。fixtures为合成资料，不连接真实企业或个人。
来源协议、字段身份及当前边界见[来源契约](docs/source-contract.md)。Python 3.14，现有代码仅使用标准库。

读取当前响应概览：

    python -m app.service fixtures/sample.json

执行授权目录采集（任务JSON含source_id、entry、scope{origin,path_prefix}，可选max_pages、accept）：

    python -m app.catalog job.json

运行现有模块检查：

    python -m unittest discover -s tests -v

检查源码编译：

    python -m compileall -q app tests

如需单独启动本地来源，运行下列命令后读取打印的回环地址；结束时按Ctrl+C：

    python -m app.lab fixtures/sample.json
