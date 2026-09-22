# 模板与插件：接入一种新数据库

一句话：**选一个数据模型模板 → 继承它 → 只填方言原语 → `@register`**。通用探查、连接、审计都由模板/底座提供。

## 选模板

| 你的数据库像 | 继承模板 | 需要实现的原语 |
|---|---|---|
| 关系型（SQL） | `RelationalConnector` | `dbapi`、`_connect_kwargs`（元数据不同可覆盖 `list_sources/describe_source`） |
| 键值 | `KeyValueConnector` | `execute_command(*args)` |
| 文档 | `DocumentConnector` | `find/insert_one/insert_many/update_one/delete_many/count_documents/aggregate/list_collection_names` |
| 检索 / 图 / 时序 / 向量 | 对应模板 | 见各模板文件头注释 |

## 例：PostgreSQL（关系型）

```python
# dbconnector/connectors/postgres.py
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

    # PostgreSQL 的 information_schema 列名与 MySQL 略不同 → 覆盖探查
    def list_sources(self):
        return self.query(
            "SELECT table_name AS source_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() ORDER BY table_name").as_result()
```

注册：在 `connectors/__init__.py` 加 `from . import postgres`。之后 `connect("postgres", ...)` 与 MCP 通用工具立即生效。

## 例：只填一个原语的键值库

`KeyValueConnector` 把 get/set/scan/type 感知探查都用 `execute_command` 实现了，所以新键值库常常只需接一个原始命令通道（Memcached 需按自身命令做适配）。Redis 插件即如此：只有连接 + `execute_command`。

## 以独立包分发（不改本仓库）

`pip` 安装一个声明了 entry point 的包即可被自动发现：

```toml
# my-dbconnector-clickhouse/pyproject.toml
[project.entry-points."dbconnector.dialects"]
clickhouse = "my_dbconnector_clickhouse"   # 模块内 import 时执行 @register("clickhouse")
```

安装后，`available_dialects()` 与 MCP 的 `sources` 会自动列出该方言，通用工具即刻可用。

## 新插件自动继承的能力（无需写埋点/权限代码）

继承某个模板 + `@register` 后，下面这些**开箱即用**，且新方言自动适用：

- **审计**：`AUDITED_OPS` 里的方法被 `BaseConnector.__init_subclass__` 自动包装、逐次落 connector 层审计（含事务块内逐条）。覆盖某已登记方法（如 `describe_source`）也会被自动包装。
- **分级授权 / 确认**：`BaseConnector.authorize = classify → acl.decide`。新方言多数只需声明 `OP_LEVELS`（方法→风险级）；若某操作级别取决于内容（如按 SQL/命令动态判），覆写 `classify(op,args)->(level,target)`。写超 `grant` 拒绝、`[confirm_from,grant]` 走一次性令牌，全部免费继承。
- **决策审计**：≥数据写级的操作在授权点自动落 `layer=decision` 记录（放行也记），无需额外代码。
- **通用探查 / 决策预演 / 复盘 / 多源路由**：`analyze`、`ops_review`、`sources/health/...`、`source` 参数全部自动覆盖新方言。

关系型模板已内置 `classify`（按 SQL 首关键词分级）；键值/文档模板同理。所以接入一个新库，通常**只写连接 + 原语 + 一句 `OP_LEVELS`**，权限与可追溯性即到位。

## 占位符与方言差异

参数占位符记在类属性 `placeholder`（MySQL/psycopg `%s`、pymssql `%s`、SQLite `?`）；SQL 与元数据视图差异放在插件里（覆盖探查方法即可），模板不假设方言细节。
