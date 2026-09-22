# 架构：三层结构

db-connector 的目标是**给 agent 用的数据库连接器底座**——一种数据模型对应一个模板，一个具体数据库是一个插件。新增数据库不改动底座。

```
第一层  BaseConnector        跨方言统一契约（生命周期 + 通用探查）
第二层  templates.<族>        按数据模型定义"操作面 + 通用探查如何映射"
第三层  connectors.<方言>      只填方言原语的插件，继承某个模板
```

## 第一层 BaseConnector（`dbconnector/base.py`）

所有数据模型共同满足的最小接口：

- 生命周期：`ensure_ready() / close() / ping() / health_check()` + 上下文管理。
- 通用探查（**跨方言统一**，agent 学一次到处用）：
  - `list_sources()` —— 有哪些数据单元（表 / 集合 / key / index…）
  - `describe_source(name)` —— 某单元结构
  - `get_source(name, limit)` —— 取样
- `data_model` 属性：所属族名（relational / keyvalue / document / columnar / search / graph / timeseries / vector）。
- `__init_subclass__`：按类声明的 `AUDITED_OPS` 自动给这些方法套上审计包装（见"审计"文档），插件零埋点。

## 第二层 模板（`dbconnector/templates/`）

每种数据模型一个模板，职责是**把"原生能力"翻译成第一层的通用探查**，并提供该族的高层操作。插件不必各自实现这些映射。

| 模板 | 插件要实现的原语 | 模板提供的能力 |
|---|---|---|
| `RelationalConnector` | `dbapi`、`_connect_kwargs` | 连接池、`query/execute/execute_many/transaction`、SQL 通用探查 |
| `ColumnarConnector` | 同上 | 关系型能力 + OLAP 采样护栏、`estimate_scan/list_partitions` |
| `KeyValueConnector` | `execute_command(*args)` | `get/set/delete/exists/scan/command` + 类型感知 `get_source` + 探查 |
| `DocumentConnector` | `find/insert_*/update_one/delete_many/count_documents/aggregate/list_collection_names` | `delete/count` 派生 + 通用探查 |
| `Search/Graph/TimeSeries/Vector` | 各自原语 | 映射到统一探查契约 |

关键原则：**通用探查逻辑归模板，方言差异只填原语**。例如 Redis 的"按 key 类型读取"、Mongo 的"列集合/看索引"都在模板里用原语统一实现，Redis/Mongo 插件只剩"建连 + 原始命令/驱动调用"。

## 第三层 插件（`dbconnector/connectors/`）

一个具体数据库 = 一个继承模板、`@register("<dialect>")` 的类。已内置：`mysql`、`redis`、`mongodb`。

## 注册与发现（`dbconnector/registry.py`）

- `@register(dialect)`：把插件登记到方言名。
- `connect(dialect, **cfg)` / `create(ConnectorConfig)`：工厂实例化。
- 内置插件在 `connectors/__init__.py` 导入即注册；**外部插件**通过 pip entry_points 组 `dbconnector.dialects` 声明，`registry` 首次使用时自动发现——底座零改动即可被第三方扩展。

## MCP server（`mcp_server/`）

把上述底座暴露为 stdio MCP server，是 agent 的入口，也是**策略与审计的使用层**：多源配置、读写分离分级授权 + 确认、双边界审计日志。详见 `permissions.md` 与 `audit.md`。
