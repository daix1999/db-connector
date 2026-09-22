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

`dbconnector.registry` 首次使用时加载 `dbconnector.dialects` 组，`available_dialects()` 与 MCP 的 `sources` 会自动列出。

## 审计自动接入

子类声明 `AUDITED_OPS` 里的方法会被 `BaseConnector.__init_subclass__` 自动包装、逐次落审计——插件无需写任何埋点代码。若覆盖了某个已登记方法（如 `describe_source`），覆盖版也会被自动包装。

## 占位符与方言差异

参数占位符记在类属性 `placeholder`（MySQL/psycopg `%s`、pymssql `%s`、SQLite `?`）；SQL 与元数据视图差异放在插件里（覆盖探查方法即可），模板不假设方言细节。
