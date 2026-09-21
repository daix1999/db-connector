# db-connector · 可插拔本地数据库连接器（Python · 多方言）

一个**两层抽象**的数据库连接器库：
- **第一层 `BaseConnector`**：所有后端共同满足的极简契约（生命周期 + `list_sources / describe_source / get_source / health`）。
- **第二层按数据模型分族**：`RelationalConnector`（SQL，旧名 `DBConnector` 兼容）· `KeyValueConnector`（Redis）· `DocumentConnector`（Mongo）。

内置三种方言：**MySQL、Redis、MongoDB**；新增 Postgres/SQLServer 只要继承 `RelationalConnector` 写一个子类并 `@register`，调用方零改动。

> 设计取向（按头儿要求）：**连接器本身能力全开**（读写/危险命令都提供），**只读限制只在"使用层"（MCP guard）按运行时开关裁剪**——不把权限写死进连接器。

内置能力：连接池/连接管理 · 参数化查询 · 批量 · 事务 · 通用探查 · 健康检查 · 上下文管理。

## 目录结构

```
db-connector/
├── dbconnector/
│   ├── __init__.py          # 对外 API：connect / create / register / available_dialects ...
│   ├── base.py              # 第一层 BaseConnector + 第二层 RelationalConnector(=DBConnector)
│   ├── nosql.py             # 第二层 KeyValueConnector / DocumentConnector 中间基类
│   ├── config.py            # ConnectorConfig / PoolConfig，支持 kwargs 与环境变量
│   ├── registry.py          # 方言注册表 + 工厂（可插拔核心）
│   ├── result.py            # Result(通用) + ResultSet(关系型兼容)
│   ├── exceptions.py        # 统一异常层级
│   └── connectors/
│       ├── mysql.py         # MySQLConnector   → RelationalConnector
│       ├── redis.py         # RedisConnector  → KeyValueConnector
│       └── mongodb.py       # MongoConnector  → DocumentConnector
├── mcp_server/              # 把连接器包装成多方言 stdio MCP server
├── scripts/smoke_test.py    # test 库引导 + 关系型端到端冒烟
├── examples/demo.py         # 使用示例
├── tests/                   # 离线单测 + 关系型 stdio + 多方言护栏 stdio
├── requirements.txt
└── .env.example
```

## 安装依赖

```bash
pip install -r requirements.txt      # PyMySQL + DBUtils
```

## 快速开始

```python
from dbconnector import connect

with connect("mysql", host="127.0.0.1", port=3306,
             user="root", password="你的密码", database="test") as db:

    # 查询（参数化，防注入；MySQL 用 %s 占位符）
    res = db.query("SELECT id, name FROM users WHERE score >= %s", (60,))
    print(res.columns)        # ['id', 'name']
    print(res.to_list())      # [{'id':1,'name':'alice'}, ...]
    print(res.scalar)         # 第一行第一列

    # 单条 DML -> 受影响行数；INSERT 想要自增主键用下面这个
    new_id = db.execute_returning_id(
        "INSERT INTO users(name, score) VALUES (%s, %s)", ("bob", 70))

    # 批量
    db.execute_many("INSERT INTO users(name, score) VALUES (%s, %s)",
                    [("c", 80), ("d", 90)])

    # 事务：正常结束提交，抛异常自动回滚
    with db.transaction() as tx:
        tx.execute("UPDATE users SET score=%s WHERE id=%s", (99, new_id))
        tx.execute("INSERT INTO audit(msg) VALUES (%s)", ("bumped",))
```

从环境变量读配置（对应 `.env.example`）：

```python
from dbconnector import ConnectorConfig, create
db = create(ConnectorConfig.from_env("mysql", prefix="DB_"))
```

## 跑冒烟测试（准备 test 库 + 全链路验证）

脚本会先 `CREATE DATABASE IF NOT EXISTS`，建临时表，逐项验证 CRUD / 批量 / 事务回滚 / 事务提交，
默认测完自动 `DROP` 临时表（加 `--keep` 可保留）。

```bash
python scripts/smoke_test.py --user root --password "你的MySQL密码" --database test
```

也可用环境变量：`set DB_PASSWORD=... && python scripts/smoke_test.py`

离线单元测试（不需要数据库）：

```bash
python tests/test_offline.py     # 或 pytest -q
```

## API 速查

| 方法 | 作用 | 返回 |
|------|------|------|
| `query(sql, params)` | SELECT | `ResultSet` |
| `fetch_one(sql, params)` | 取一行 | `dict \| None` |
| `fetch_value(sql, params)` | 取一个标量 | `Any` |
| `execute(sql, params)` | 单条 DML | 受影响行数 `int` |
| `execute_returning_id(...)` | INSERT 取自增主键 | `int \| None` |
| `execute_many(sql, seq)` | 批量 DML | 累计行数 `int` |
| `transaction()` | 事务上下文 | `tx.execute/query/executemany` |
| `ping()` / `health_check()` | 连通性 / 健康信息 | `bool` / `dict` |

## 作为 MCP Server 让 agent 直接调用

`mcp_server/` 把连接器包装成标准 **stdio MCP server**，**一个进程可同时集成多个数据源**（MySQL + Redis + MongoDB…），由 env `DB_SOURCES`（JSON 数组）配置；**默认只读**（写需该源 `allow_write=true`）。也保留旧单方言用法（只给 `DB_DIALECT/DB_HOST/...`）。每个工具带可选 `source` 参数定位到某个源，只有一个源时可省略；族专属工具会校验目标源的方言族，不匹配清晰报错。

工具三类：

**源发现 + 通用（跨方言）**
| 工具 | 作用 |
|------|------|
| `sources` | 列出所有源：名称/方言/族/是否可写 |
| `health(source)` | 某源连通性 / 族 / 是否放开写 |
| `list_sources(source)` | 列数据（表 / 集合 / key 概览） |
| `describe_source(name, source)` | 结构（字段 / 索引 / key+TTL） |
| `get_source(name, limit, source)` | 样本 |

**族专属（按目标源的方言族校验）**
| 方言族 | 工具 | 只读? |
|------|------|:---:|
| 关系型 | `query(sql,source)` / `execute(sql,source)` | query 只读，execute 需该源放开写 |
| Redis | `redis_get` / `redis_scan` / `redis_command(name,args,source)` | command 只读放行白名单，写/危险命令受控 |
| Mongo | `mongo_find` / `mongo_count` / `mongo_aggregate` / `mongo_write(source)` | 前三个只读；mongo_write 及含 `$out/$merge` 的管道需放开写 |

**安全护栏**（`mcp_server/guard.py`，已离线单测覆盖）：SQL 只读白名单 + 多语句/`INTO OUTFILE` 拦截 + 表名正则；Redis 只读命令白名单 + 危险命令(FLUSHALL/CONFIG/SHUTDOWN…)始终拒绝；Mongo 只读操作判定 + 写型管道识别。越权统一返回规范 `ToolError`。

**多源配置**（推荐，env `DB_SOURCES` 传 JSON 字符串）：
```json
[{"name":"mysql","dialect":"mysql","host":"127.0.0.1","port":3306,"user":"root","password":"...","database":"test"},
 {"name":"redis","dialect":"redis","host":"127.0.0.1","port":6379,"database":"0"},
 {"name":"mongo","dialect":"mongodb","host":"127.0.0.1","port":27017,"database":"app"}]
```
`allow_write`/`max_rows` 可按源单独设；全局 `DB_ALLOW_WRITE`/`DB_MAX_ROWS` 兜底。旧单方言：`set DB_DIALECT=mysql & set DB_PASSWORD=... & python -m mcp_server.server`。

在 千问办公 / Claude / Cursor 客户端注册：把 `mcp_config.example.json` 里 `mcpServers.db-connector` 段合并进 MCP 配置，改好 `cwd` 与 `env`。（注：千问办公出于安全不允许 agent 自动注册 stdio MCP，需手动粘贴。）

跑测试：

```bash
python tests/test_mcp_stdio.py --user root --password "你的密码"      # 关系型真实端到端
python tests/test_mcp_multidialect.py                                # Redis/Mongo 护栏（无需服务）
python tests/test_guard.py && python tests/test_guard_nosql.py && python tests/test_offline.py
```

## 快速使用 Redis / Mongo（库层 API，能力全开）

```python
from dbconnector import connect

# Redis
with connect("redis", host="127.0.0.1", port=6379, database=0) as r:
    r.set("user:1:name", "头儿", ttl=3600)      # 写
    print(r.get("user:1:name"))                 # 读
    print(r.scan(match="user:*", count=100))    # 只读遍历

# MongoDB
with connect("mongodb", host="127.0.0.1", user="admin",
             password="xxx", database="app") as m:
    m.insert_one("orders", {"sku": "ThinkPad", "qty": 2})
    print(m.find("orders", {"sku": "ThinkPad"}, limit=5))
    print(m.count("orders"))
```

## 扩展一种新数据库（可插拔示例）
新增 `Postgres` 只需一个文件 `dbconnector/connectors/postgres.py`：

```python
from ..base import RelationalConnector      # 关系型族基类（DBConnector 是其兼容别名）
from ..registry import register

@register("postgres")
class PostgresConnector(RelationalConnector):
    dialect = "postgres"
    default_port = 5432

    @property
    def dbapi(self):
        import psycopg            # 依赖驱动
        return psycopg

    def _creator_name(self):
        return "connect"          # psycopg 的连接函数名

    def _connect_kwargs(self):
        c = self.config
        kw = {"host": c.host, "port": c.port or 5432,
              "user": c.user, "password": c.password, "dbname": c.database}
        kw.update(c.extra)
        return kw
```

然后在 `dbconnector/connectors/__init__.py` 里 `from . import postgres`，
即可 `connect("postgres", ...)`——**基类已经帮你实现了连接池、CRUD、事务、健康检查**，
子类不用重写任何方法。注意不同方言的占位符：MySQL 用 `%s`，psycopg 也是 `%s`，
SQL Server(pymssql) 用 `%s`，SQLite 用 `?`。

新增**非关系型**数据库则继承对应族中间基类：键值型继承 `KeyValueConnector`（照 `redis.py` 实现 `get/set/scan/command` + 三个探查方法），文档型继承 `DocumentConnector`（照 `mongodb.py` 实现 `find/insert/update/delete` + 探查方法），再 `@register("你的方言")`。

## 设计说明（确定 vs 权衡）

- **事实**：两层抽象把"跨方言公共契约"（`BaseConnector`）与"数据模型族能力"（关系/键值/文档）分离。关系型方言差异只体现在 `dbapi` + `_connect_kwargs`（+ 可选 `_creator_name`）三处；`list_sources/describe_source/get_source` 有默认实现，方言可覆盖（如 Oracle/Mongo 元数据不同）。
- **事实**：连接器能力全开、不含权限判断；读写裁剪全部下沉到 `mcp_server/guard.py` 使用层，按 `DB_ALLOW_WRITE` 与各只读白名单运行时生效——库层与 agent 层职责清晰。
- **权衡**：`PooledDB` 默认 `ping=1`（每次取连接探活）偏稳健，牺牲一点吞吐换取"连接被服务端回收后自动重连"；可 `PoolConfig.ping=4` 提速。
- **边界**：`close()` 对关系型仅置空池引用交 GC 归还；长驻服务退出前建议显式调用。Redis 无服务时构造不连接、命令时才报错，故无服务也能验证护栏。本库面向"查询 + 轻写入"，不含 ORM / 迁移 / 分库分表。
