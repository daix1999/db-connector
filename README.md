# db-connector

**为 AI Agent 设计的可插拔数据库连接器**，以 stdio MCP server 对外提供。评判标准不是"人写代码方便"，而是"agent 调用顺手"：接口自描述、跨方言统一、结果结构化、失败可据以决定下一步。

底层是两层抽象的 Python 库（连接器能力全开，读写皆支持），安全与权限全部收敛在使用层（MCP server）。

> 详细文档见 `docs/`：[架构](docs/architecture.md) · [模板与插件](docs/templates-and-plugins.md) · [读写分离与分级授权](docs/permissions.md) · [操作审计](docs/audit.md)

## 一分钟理解

- **三层**：`BaseConnector`（统一契约）→ `templates/<数据模型>`（把原生能力映射成通用探查）→ `connectors/<方言>`（只填原语的插件）。加一种库 = 选模板 + 写插件，底座不动。
- **一个进程挂多源**：MySQL + Redis + MongoDB 共存，工具用 `source` 参数路由；族专属工具会校验目标源方言。
- **读写分离分级授权**：读默认放行；写按每源 `grant`（read → read+data → read+schema → read+destructive）+ 库白/黑名单控制；超过免确认上限的操作返回一次性确认令牌，agent 带令牌二次调用才执行；管理员级命令恒拒。
- **双边界审计**：connector 层记真实落库操作（含库直调），mcp 层记 agent 意图与被拒/待确认的调用，默认脱敏。

## 支持的数据模型

| 模型 | 模板 | 已实现方言 | 代表库 |
|---|---|---|---|
| 关系 | RelationalConnector | **mysql** | PostgreSQL, SQL Server, Oracle, SQLite, TiDB |
| 列式/OLAP | ColumnarConnector | 模板 | ClickHouse, Doris, StarRocks |
| 键值 | KeyValueConnector | **redis** | Memcached, etcd |
| 文档 | DocumentConnector | **mongodb** | CouchDB, DynamoDB |
| 检索 | SearchConnector | 模板 | Elasticsearch, OpenSearch |
| 图 | GraphConnector | 模板 | Neo4j, NebulaGraph |
| 时序 | TimeSeriesConnector | 模板 | InfluxDB, QuestDB |
| 向量 | VectorConnector | 模板 | Milvus, Qdrant, pgvector |

加新方言：见 [docs/templates-and-plugins.md](docs/templates-and-plugins.md)。

## 安装

```bash
pip install -r requirements.txt        # 关系型核心 + MCP
pip install ".[redis,mongo,mcp]"       # 按需装可选方言
```

## 作为库用

```python
from dbconnector import connect
with connect("mysql", host="127.0.0.1", user="root", password="...", database="test") as db:
    db.query("SELECT * FROM t WHERE id=%s", (1,))
    with db.transaction() as tx:            # 块内每条语句都进审计
        tx.execute("UPDATE t SET x=%s WHERE id=%s", (2, 1))
```

Redis / Mongo 同理：`connect("redis", ...)` / `connect("mongodb", ...)`。

## 作为 MCP server（agent 入口）

一个进程按 `DB_SOURCES`（JSON）挂多源；工具带 `source` 参数路由。

- 通用（跨方言）：`sources / health / list_sources / describe_source / get_source`
- 决策预演：`analyze`（执行前静态预演风险/授权判定/建议，与执行同源、不连库、不发令牌；CLI 版 `scripts/ops_analyze.py`）
- 关系族：`query`（只读）/ `execute`（写，按授权）
- 键值族：`redis_get / redis_scan / redis_command`
- 文档族：`mongo_find / mongo_count / mongo_aggregate / mongo_write`

配置示例（含每源 `access` 权限块）：

```jsonc
[
  {"name":"mysql","dialect":"mysql","host":"127.0.0.1","port":3306,"user":"root","password":"...","database":"test"},
  {"name":"redis","dialect":"redis","host":"127.0.0.1","port":6379,"database":"0","access":{"grant":"read"}},
  {"name":"mongo","dialect":"mongodb","host":"127.0.0.1","port":27017,"database":"app","access":{"grant":"read+data","allow_escalation":true}}
]
```

权限完整写法见 [docs/permissions.md](docs/permissions.md)，审计见 [docs/audit.md](docs/audit.md)。在 千问办公 / Claude / Cursor 注册：把 `mcp_config.example.json` 合并进 MCP 配置（千问办公不允许 agent 自动注册 stdio MCP，需手动粘贴）。

## 测试

```bash
python tests/test_offline.py                         # 注册表/配置/结果
python tests/test_guard.py && python tests/test_guard_nosql.py
python tests/test_acl.py                            # 根层分级授权(所有模板共用)
python tests/test_analyzer.py                       # 操作决策分析(静态卡/放行写也记决策)
python tests/test_config_profiles.py                # 权限档 profile/文件加载
python tests/test_ops_review.py                     # 操作复盘(按风险级过滤/聚合)
python tests/test_audit.py                            # 脱敏/成功/拒绝/事务逐条
python tests/test_permissions.py --user root --password ...   # 分级授权+确认流(需容器)
python tests/test_mcp_stdio.py --user root --password ...     # 关系型真实端到端
python scripts/smoke_test.py --user root --password ... --database test
```

## 版本与许可

1.x 稳定线。2.0.0：模板收口、读写分离分级授权 + 一次性确认令牌、分层文档。2.1.0：分级授权流程下沉到根（`dbconnector/acl.py` + `BaseConnector.authorize`），所有模板共用同一套 classify→decide 管线。2.2.0：权限档 profile + 同一插件多环境各配权限（授权对象=连接 source，无角色）。2.2.1：权限档可外置到 access_profiles.json。2.3.0：权限收敛为两个旋钮 grant(硬上限)+confirm_from(确认起点)，去掉 allow_escalation/confirm_above(旧配置自动兼容映射)。2.4.0：新增操作决策分析（`analyze` 工具 + `scripts/ops_analyze.py`，静态预演、与执行同源）；写操作无条件落 `layer=decision` 审计（放行也记判了什么）；修复 `classify_sql` 把 `UPDATE…SET` 误判为 ADMIN 的 bug。 2.5.0：新增操作复盘 CLI `scripts/ops_review.py`（按风险级/源/判定/时间过滤+聚合）。向后兼容（`DBConnector` 别名、`nosql` 垫片、`allow_write` 映射）。许可证：MIT。
