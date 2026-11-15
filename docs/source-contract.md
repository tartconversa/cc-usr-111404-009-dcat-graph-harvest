# 来源和当前接口

## 原始响应

fixtures/sample.json含source_id、url、status、headers、body_base64和可选metadata，headers为有序二元列表，保留重复字段；body_base64解码成真实原始实体字节，原件也存在fixtures/source-body.bin。
app.source.from_packet校验状态、头与字节编码，生成只读Response。header(name)不区分字段名大小写并按顺序逗号连接重复值，headers原元组始终保留。
sha256只证明当前实体字节，不证明来源业务身份或采集完整。fetch执行一次HTTP请求，保留错误响应，不自动跳转，不替来源解释缓存、分页或业务错误。
fetch(url,source_id,method,headers,body,max_bytes)保留方法和请求体，读取超过预算即失败；适配器现有超时为5秒。app.service.collect_one调用该来源接口，inspect(packet)供已有响应读取，二者输出source_id、response_sha256和summary。
实际访问资格由上层来源配置控制，本地来源服务只绑定127.0.0.1；其/source路径返回指定响应，其他路径404。服务器记录请求方法、路径、头与body_base64；现有记录只用于合成来源联调，不包含真实凭据。

## 业务来源口径

来源支持Turtle及N-Quads，按Content-Type选择解析，Turtle相对IRI以有效响应URL或合法@base解释；N-Quads原图项保留。采集dcat:Dataset、dcat:distribution、dcat:Distribution、dcat:accessURL、dcat:downloadURL、dct:title和dct:license，未识别谓词仍可保留原断言。默认图和命名图不混同。HTTP Link rel=next是唯一分页发现入口，访问范围由配置origin+path前缀限制。重试身份绑定source_id、有效URL、表示选择和原始响应摘要；对不同响应的空白节点不推断同一。

## 目录采集口径

app.catalog.harvest(entry,source_id,scope,fetcher,store,max_pages,accept)从入口开始，仅跟随2xx响应的Link rel=next，且目标须落在Scope(origin,path_prefix)内；目录中的许可、accessURL、downloadURL、sameAs等任意IRI只记录为断言或未闭合关系，不作为抓取入口。请求带Accept协商头，报告逐页记录有效URL、状态、Content-Type、Content-Location、Vary、实体摘要与解析基址（Content-Location优先，其次有效URL，文档内@base另记为declared_base），表示版本可追溯。
项身份：IRI按解析结果保留；空白节点身份为（文档作用域,标签)，作用域由source_id、有效URL、响应摘要派生——同一响应重试身份不变、不凭新造ID增加资料，不同文档即使标签相同也不合并；字面量保留词形、语言标记（小写规范化）与数据类型，显示文本不作为身份。owl:sameAs只记录不合并。
app.store.GraphStore按断言记录其出现的文档作用域；当前视图按（来源,页面）取最新一次成功解析，页面换版后旧作用域断言退出视图但留档（documents中current=false），重取失败保留上一版资料。分发可被多个数据集共享（同一IRI即同一分发）；accessURL与downloadURL分列；缺许可或访问方式标missing，不替来源补声明。
报告complete仅在没有缺口时为真；页面失败（fetch-error、http-error、redirect-not-followed、unsupported-media、parse-error）或分页预算耗尽（budget-exhausted）记入gaps，仍交付已有数据集、分发及open_references中的未闭合关系，不宣称目录齐全。app.service.collect_catalog与python -m app.catalog job.json为两个入口。

## 当前能力与配置

当前app.baseline.analyze只产生一份响应的概览，兼容入口继续保留；新采集可增加入口，不能把字节摘要等同于协议或业务完整性。
metadata={}。读取预算、授权范围和来源凭据属于调用配置，真实密钥不放在资料目录或交付证据中。
