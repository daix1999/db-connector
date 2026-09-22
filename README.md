# db-connector

面向 AI Agent 的可插拔数据库连接器，以 stdio MCP server 的形式对外提供能力。项目的评判标准不是"人写代码是否方便"，而是"Agent 调用是否顺手"：接口自描述、跨方言统一、结果结构化、失败信息可据以决定下一步动作。

底层是一个两层抽象的 Python 库（连接器能力完整，读写皆支持）；权限与安全裁剪全部收敛在使用层（MCP server 的 guard），因此同一套连接器既能给 Agent 安全地只读使用，也能在显式授权下承担写操作。

## 1. 架构

分三层。加一种数据库，就是"选一个模板、写一个方言插件"，不改动底座。

```
dbconnector/
  base.py            第一层  BaseConnector    —— 跨方言统一契约：生命周期 + list_sources/describe_source/get_source
  templates/         第二层  类型模板          —— 按数据模型定义操作面
    relational.py        RelationalConnector（SQL：池/查询/批量/事务）
    columnar.py          ColumnarConnector （OLAP：扫描护栏、分区、代价估算）
    keyvalue.py          KeyValueConnector
    document.py          DocumentConnector
    search.py            SearchConnector
    graph.py             GraphConnector
    timeseries.py        TimeSeriesConnector
    vector.py            VectorConnector
  connectors/        第三层  方言插件          —— 继承模板实现具体数据库
    mysql.py  redis.py  mongodb.py
  registry.py        注册表 + 工厂 + 插件发现（内置 import + pip entry_points）
  config.py  result.py  exceptions.py
mcp_server/          面向 Agent 的 stdio MCP server + 安全护栏
```

设计要点：

- 第一层的探查方法（列数据单元、看结构、取样）对所有数据模型统一。Agent 用 `list_sources / describe_source / get_source` 就能摸清任意库，无需为每种数据库学一套新 API。
- 第二层把"同一种数据模型"的操作面固定下来。SQL 类库共享连接池、参数化查询、事务；换方言只需提供驱动与连接参数。
- 第三层是插件。内置插件放在 `connectors/` 并在包导入时注册；外部插件可通过 Python entry_points 组 `dbconnector.dialects` 注入，底座代码不变。
- 向后兼容：`DBConnector` 是 `RelationalConnector` 的别名；`dbconnector.nosql` 保留为转发垫片。

## 2. 支持的数据模型与数据库

模板为契约，方言为实现。当前已实现并实测的关系型/键值/文档三种可直接使用；其余模板给出统一操作面，按同一"选模板 + 写方言"路径接入即可。

| 数据模型 | 类型模板 | 查询/交互语言 | 已实现方言 | 可接入的代表库 |
|---|---|---|---|---|
| 关系 | RelationalConnector | SQL（各家方言） | MySQL/MariaDB | PostgreSQL, SQL Server, Oracle, SQLite, TiDB |
| 列式/OLAP | ColumnarConnector | SQL（分析向） | —（模板） | ClickHouse, Doris, StarRocks, Druid, Greenplum |
| 键值 | KeyValueConnector | Redis RESP 命令 | Redis | Memcached, etcd |
| 文档 | DocumentConnector | 查询文档 / 聚合管道 | MongoDB | CouchDB, DynamoDB |
| 检索 | SearchConnector | 查询串 / DSL | —（模板） | Elasticsearch, OpenSearch, Solr |
| 图 | GraphConnector | Cypher / GQL / Gremlin | —（模板） | Neo4j, NebulaGraph, JanusGraph, Neptune |
| 时序 | TimeSeriesConnector | 行协议 / FLUX / SQL | —（模板） | InfluxDB, QuestDB, TimescaleDB, TDengine |
| 向量 | VectorConnector | 向量检索 API | —（模板） | Milvus, Qdrant, Weaviate, Chroma, pgvector |

不同数据模型的连接层差异被模板吸收：关系型统一走 DB-API 2.0 + 连接池；Redis、Mongo 用各自驱动自带的连接池，不套 DB-API；向量/检索/图/时序以"集合/索引/标签/measurement"映射到统一的 `list_sources/describe_source/get_source`。

## 3. 安装

```bash
pip install -r requirements.txt          # 关系型 + MCP
pip install ".[redis,mongo,mcp]"         # 按需装可选方言
```

依赖：Python ≥ 3.10；`PyMySQL`、`DBUtils`（关系型核心）；`redis`、`pymongo`（NoSQL，可选）；`mcp ≥ 2.0`（server）。

## 4. 作为库使用

```python
from dbconnector import connect

with connect("mysql", host="127.0.0.1", user="root", password="...", database="test") as db:
    rows = db.query("SELECT * FROM users WHERE id > %s", (0,)).to_list()
    db.execute("UPDATE users SET name=%s WHERE id=%s", ("bob", 1))
    with db.transaction() as tx:                 # 正常提交，异常回滚
        tx.execute("INSERT INTO audit(msg) VALUES (%s)", ("edited",))
```

```python
with connect("redis", host="127.0.0.1", database=0) as r:
    r.set("k", "v", ttl=60); r.scan(match="k*")

with connect("mongodb", host="127.0.0.1", database="app") as m:
    m.insert_one("orders", {"sku": "X1", "qty": 2}); m.find("orders", {"qty": {"$gt": 1}})
```

## 5. MCP Server（Agent 入口）

一个进程可同时挂载多个数据源（不同方言并存），通过环境变量 `DB_SOURCES`（JSON 数组）配置；也兼容旧的单方言 `DB_DIALECT/DB_HOST/...`。每个工具带可选 `source` 参数定位到某个源，只有一个源时可省略。族专属工具会校验目标源的方言族，不匹配则返回可操作的错误。

### 推荐的 Agent 调用顺序

1. `sources` — 看有哪些源、每个源属于哪个族，以及系统已注册的方言清单。
2. `list_sources(source)` — 列出该源的数据单元（表/集合/key）。
3. `describe_source(name, source)` — 看结构（字段/索引/类型）。
4. 取数：通用用 `get_source`；关系型用 `query`；键值用 `redis_*`；文档用 `mongo_*`。

多个工具的返回里带 `next`/`hint` 字段，提示下一步该调什么，减少 Agent 的探索成本。

### 工具清单

| 工具 | 适用族 | 说明 |
|---|---|---|
| `sources` | 通用 | 已配置源 + 注册方言目录 |
| `health(source)` | 通用 | 连通性、方言、族、是否可写 |
| `list_sources(source)` | 通用 | 列数据单元 |
| `describe_source(name,source)` | 通用 | 数据结构 |
| `get_source(name,limit,source)` | 通用 | 取样，免写查询 |
| `query(sql,source)` | relational | 只读 SQL，自动补 LIMIT |
| `execute(sql,source)` | relational | 写 SQL，需该源 `allow_write=true` |
| `redis_get / redis_scan / redis_command(name,args,source)` | keyvalue | 读、扫描、任意命令（受护栏约束） |
| `mongo_find / mongo_count / mongo_aggregate / mongo_write(source)` | document | 查询、计数、聚合、写入 |

新增方言插件后，通用工具（`sources/health/list_sources/describe_source/get_source`）自动对它可用，无需为本文件增改代码；只有该族特有的动作才需要新增对应工具。

### 配置

```jsonc
// DB_SOURCES（作为环境变量传的是 JSON 字符串）
[
  {"name":"mysql","dialect":"mysql","host":"127.0.0.1","port":3306,"user":"root","password":"...","database":"test","allow_write":false},
  {"name":"redis","dialect":"redis","host":"127.0.0.1","port":6379,"database":"0"},
  {"name":"mongo","dialect":"mongodb","host":"127.0.0.1","port":27017,"database":"app"}
]
```

`allow_write`、`max_rows` 可按源单独设置；全局默认用 `DB_ALLOW_WRITE`、`DB_MAX_ROWS`。连接串复杂的库（如 MongoDB 带鉴权源）用 `dsn` 字段。

## 6. 安全模型

核心原则：连接器能力完整、不做权限判断；安全在使用层实施。这样既能给 Agent 一个安全的只读入口，也能在显式授权下放开写。

已实施的护栏（`mcp_server/guard.py`）：

- 只读模式：`query` 仅放行 SELECT/SHOW/DESC/EXPLAIN；识别并拒绝多语句拼接、`INTO OUTFILE`、`SET` 等；SELECT 无 LIMIT 时按 `max_rows` 自动补，限制回传体量。
- 写操作：`execute` 及 Redis/Mongo 写工具要求对应源 `allow_write=true`；关系型 `execute` 拒绝一次多条语句。
- Redis：只读命令白名单；`FLUSHALL / FLUSHDB / CONFIG / SHUTDOWN / DEBUG / KEYS / RENAME` 等即便在写模式下也始终拒绝（`KEYS` 排除在大库上会阻塞服务，改用 `SCAN`）。
- Mongo：聚合管道含 `$out/$merge` 视为写；`mongo_write` 仅接受显式白名单操作。
- 标识符：表名等走字符白名单，避免拼进元数据查询造成注入；参数一律走驱动的参数化。

### 操作审计日志

审计下沉到连接器（`dbconnector/audit.py`，库层），因此**无论走 MCP server 还是直接 `connect(...).query(...)`，落库操作都留痕**。MCP 层（`mcp_server/audit.py`）复用同一核心，额外记录"agent 意图 + 被护栏拦截的尝试"。两层共用一个 logger 实例（一把锁写同一个文件），并发下不串行交错。

- 连接器层：`BaseConnector.__init_subclass__` 按各模板声明的 `AUDITED_OPS` 自动包装操作，无需在方言里写埋点；identity 带 `dialect / data_model / label / host / database`。
- MCP 层：每个工具经 `@audited` 记录，含被 guard 拒绝的调用（这类不会落到连接器层）。
- 记录字段：`ts, layer(mcp|connector), source/label, dialect, op, args(脱敏), outcome(ok|denied|error), dur_ms, detail(结果量)`。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `DB_AUDIT` | `on` | 总开关，`off/0/false` 关闭 |
| `DB_AUDIT_LAYER` | `all` | `all` \| `connector` \| `mcp` \| `off` 分层开关 |
| `DB_AUDIT_LOG` | `logs/db-connector-audit.jsonl` | 路径；该目录已 `.gitignore`，含 SQL 不入库 |
| `DB_AUDIT_PARAMS` | 关 | `1` 时记录参数值（敏感）；默认只记形状 |

脱敏：保留 SQL 文本与标识符，`params/payload/filter` 等含业务值的参数默认只记形状；绝不记录连接凭据；审计写失败不影响主调用。轮转由部署侧处理。

已知边界：显式 `transaction()` 块内的单条语句暂不逐条审计（记录到"开启了一次事务"这一层）。

运行须知（判断，非缺陷但需部署时考虑）：

- 凭据以环境变量注入到连接器进程，属明文。仅在本机或受信主机使用；不要把 `DB_SOURCES` 提交进版本库（`.gitignore` 已排除 `.env`）。
- 驱动默认明文连接本机。若连远程实例，请在对应源 `extra` 里开启 TLS（PyMySQL `ssl_ca`、Redis `ssl=True`、pymongo `tls=True`）。
- Redis 写路径的命令名需匹配驱动方法（如删除用 `DEL` 在部分驱动下不等价 `delete`）；如需稳定批量写，建议为该族补专用写工具而非直接透传。

## 7. 扩展：接入一种新数据库

第一步，选数据模型对应的模板。以关系型 PostgreSQL 为例，新建 `dbconnector/connectors/postgres.py`：

```python
from ..registry import register
from ..templates.relational import RelationalConnector

@register("postgres")
class PostgresConnector(RelationalConnector):
    default_port = 5432
    placeholder = "%s"

    @property
    def dbapi(self):
        import psycopg
        return psycopg

    def _connect_kwargs(self):
        c = self.config
        kw = {"host": c.host, "port": c.port or 5432, "user": c.user,
              "password": c.password, "dbname": c.database}
        kw.update(c.extra)
        return kw
```

在 `connectors/__init__.py` 增加对它的导入即可被 `connect("postgres", ...)` 使用；池、查询、事务、通用探查都由模板提供。若元数据视图不同（非 MySQL 的 information_schema），覆盖 `list_sources/describe_source` 即可。

非关系型同理：键值继承 `KeyValueConnector`（实现 `get/set/delete/exists/scan/command` + 三个探查方法），文档继承 `DocumentConnector`（实现 `find/insert_one/insert_many/update_one/delete/count`）。

以第三方包分发（不改本仓库）：在包的 `pyproject.toml` 声明

```toml
[project.entry-points."dbconnector.dialects"]
clickhouse = "my_plugin.clickhouse"
```

安装后，`available_dialects()` 与 MCP 的 `sources` 会自动列出该方言，通用工具即刻可用。

## 8. 测试

```bash
python tests/test_offline.py            # 注册表/配置/结果，无依赖
python tests/test_guard.py              # SQL 只读护栏
python tests/test_guard_nosql.py        # Redis/Mongo 护栏
python tests/test_audit.py              # 审计日志：脱敏/成功/拒绝/开关
python tests/test_mcp_multidialect.py   # 族守卫/只读拒绝（无需真实服务）
python tests/test_mcp_stdio.py --user root --password ...          # 关系型真实端到端
python scripts/smoke_test.py --user root --password ... --database test   # 关系型全链路冒烟
```

## 9. 版本与许可

版本 1.0.0 起为稳定基线；1.1.0 引入类型模板分层（`templates/`）、entry_points 插件发现、MCP 多源与面向 Agent 的自描述/安全增强；1.2.0 加入使用层操作审计；1.3.0 将审计下沉到 BaseConnector，库直调与 MCP 双边界统一留痕。均向后兼容。许可证：MIT。
