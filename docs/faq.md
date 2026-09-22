# FAQ / 排障

## 概念

**Q：为什么"能力全开"的连接器却能让 agent 安全地用？**
连接器（`connect()` 返回的对象）本身读写都能干、不判权限；权限/确认/审计全在 MCP 使用层实施。同一套连接器既能给 agent 一个安全的只读入口，也能在你显式授权下承担写。

**Q：授权对象是什么？有角色吗？**
没有角色。授权对象是**连接目标（source）**——一个 `host:port/库` 的具体连接。`access`（grant/黑白名单/确认）挂在 source 上；MCP 工具用 `source` 参数路由。

**Q：`grant` 和 `confirm_from` 各是什么？**
`grant`=该环境可达的最高操作级（硬上限，含确认也超不过）；`confirm_from`=从哪级起需要一次性确认令牌（默认=grant，即默认不额外拦）。

**Q：新加一种数据库要做多少事？**
选一个数据模型模板 → 继承 → 只填方言原语（多数一个方法）→ `@register` 或在第三方包用 entry_points 声明。连接、通用探查、审计、分级授权、确认、决策分析、复盘全部自动继承。见 [templates-and-plugins.md](templates-and-plugins.md)。

## 权限与确认

**Q：写操作被要求确认，怎么办？**
工具返回 `requires_confirmation` + `confirm_token` + `intent`。把风险讲清、经用户同意后，用**相同参数**再调一次并附 `confirm="<token>"`。令牌默认 300 秒过期、且**绑定具体操作**——换个表/命令旧令牌无效（会再次要求确认）。

**Q：能跳过确认吗？**
提高该环境的 `grant`（到对应级即免确认）。`allow_write:true` 的老配置等价"给到破坏性且免确认"。

**Q：`FLUSHALL`/`DROP DATABASE` 为什么怎么都不让？**
ADMIN 级恒拒（grant 也无法放开），防手滑 agent 造成不可逆。要真做运维，走 DB 自己的账号/工具。

**Q：为什么 `UPDATE ... SET x=1`（无 WHERE）被判成破坏性？**
没有 WHERE 的全表 UPDATE/DELETE 客观上不可逆，`classify_sql` 会把它从 T1 升到 T3。补 WHERE 即回落到数据写级。

## 可追溯 / 复盘

**Q：被放行的写也记吗？**
记。每条 ≥数据写级的操作在授权点都落一条 `layer=decision`（op/target/level/decision/why/规则），即便 `allow`。加上 connector/mcp 两层执行记录，AI 干了什么可查。

**Q：怎么复盘？**
`python scripts/ops_review.py`（默认只看 decision 写决策）；`--min-level WRITE_DATA`、`--level DESTRUCTIVE`、`--source X`、`--decision deny`、`--group-by source`、`--html report.html`。日志默认 `logs/db-connector-audit.jsonl`（同 `DB_AUDIT_LOG`）。

**Q：日志里有敏感数据吗？**
默认脱敏：保留 SQL 文本/表名，`params/payload/filter` 等只记形状不记值；连接凭据永不记录。要全量记值设 `DB_AUDIT_PARAMS=1`（自负其责）。

## 部署与运行

**Q：Redis 里执行 `DEL`/`FLUSHALL` 报"不支持的 Redis 命令"？**
`redis_command` 按 redis-py 方法名透传；`DEL` 在部分驱动下方法名是 `delete`，而 `FLUSHALL/CONFIG` 属被拒的管理级。删除用 `redis_command` 传驱动支持的名字，或走连接器 `delete()`。写批量建议为该族补专用写工具而非裸透传。

**Q：本机 Docker 起不来 / 连不上 Redis、Mongo？**
Docker 装在 WSL2 时，**WSL 无常驻进程会被回收、把容器一起带走**。给容器加 `--restart unless-stopped`，并保一个常驻进程（如 `wsl -d <distro> -- sleep infinity` 或某服务）。端口 6379/27017 经 WSL 转发到 Windows `127.0.0.1` 一般可用；不通就以 WSL IP 连。

**Q：MySQL 8.0 与 5.7、docker 里的实例共存？**
每个实例是一个独立 source（不同 `port`/账号/库），共用同一个 mysql 插件、各挂各权限。source 名要能区分（如 `mysql8-prod`/`mysql57-sandbox`）。

**Q：能连远程库吗？**
能，但默认驱动是明文连本机。远程请在该 source 的 `extra` 里开 TLS：PyMySQL `ssl_ca`、Redis `ssl=True`、pymongo `tls=True`。并给该环境更保守的 `access`。

**Q：`analyze` 会不会绕过确认直接放行？**
不会。`analyze`（含 CLI）只做静态预演、与执行同一套 `classify→decide`，且**不签发确认令牌**；令牌只在真正执行时产生。预演结论=执行结论，可信但不可越权。

## 开发/测试

**Q：不动数据库也能测权限/审计吗？**
能。分级、授权、令牌、审计脱敏都是纯逻辑；`tests/test_acl.py`、`test_analyzer.py`、`test_audit.py`、`test_config_profiles.py`、`test_guard*.py`、`test_offline.py`、`test_ops_review.py` 全离线。`test_permissions.py`/`test_mcp_stdio.py`/`smoke_test.py` 需真实容器/服务。

**Q：改了连接器代码，已注册的 MCP 不生效？**
MCP server 是常驻 stdio 子进程，重启该连接器（或新开会话）才会加载新代码。
