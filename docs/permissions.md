# 读写分离与分级授权

设计目标：agent 默认只能读；写按"库白/黑名单 + 操作分级 + 免确认等级"控制；超过免确认上限的操作，用一次性确认令牌放行（带操作意图）。

## 实现落点：根层统一一套流程

分级授权的**流程在根层 `dbconnector`，所有模板共用一套**，不是每种方言各写各的：

- `dbconnector/acl.py`：`Access`（权限对象）+ `decide(access, level, target)`（唯一决策：allow/deny/confirm）——与 MCP、与具体数据库都无关。
- `BaseConnector.authorize(access, op, args)`：**统一管线 = `self.classify(op,args) → acl.decide(...)`**。所有连接器（含未来新方言）天然继承这套流程。
- 各模板只实现 `classify(op, args) -> (level, target)`（"我这个操作算几级、打在哪个库/表/collection/key 上"），关系/键值/文档已各自实现；新增方言只要（多数情况）声明 `OP_LEVELS` 或覆写 `classify` 即可套用同一授权流程。
- MCP 使用层（`mcp_server`）**只负责**：调 `connector.authorize()` 拿判定 + 对 `confirm` 结果签发/校验一次性令牌 + 拒绝时抛错。判定逻辑本身不在 MCP 层。

```
connector.authorize(access, op, args)
      └─ classify(op,args) → (level, target)      # 模板实现：客观分级
      └─ acl.decide(access, level, target)         # 根层唯一决策
            → allow | confirm | deny
```

下方为策略细则。

## 操作风险分级（客观，`dbconnector/levels.py`）

| 级 | 名 | 含义 | 例子 |
|---|---|---|---|
| T0 | READ | 读 | SELECT/SHOW/DESC、Redis 只读命令、Mongo find/count、通用探查 |
| T1 | WRITE_DATA | 数据写 | INSERT、UPDATE/DELETE(带WHERE)、Redis 写、Mongo insert/update/delete |
| T2 | WRITE_SCHEMA | 结构改(非破坏) | CREATE/ALTER、建索引、建集合 |
| T3 | DESTRUCTIVE | 破坏性(不可逆) | DROP、TRUNCATE、无 WHERE 的 DELETE/UPDATE、FLUSHDB、drop collection |
| T4 | ADMIN | 服务器级 | CONFIG、SHUTDOWN、GRANT/REVOKE、FLUSHALL、dropDatabase —— **永久拒绝** |

分类是客观的：`classify_sql` / `classify_redis` / `classify_mongo` 把一次操作归到某级；无 WHERE 的全表 DELETE/UPDATE 会被升到 T3。

## 授权阶梯（每源一个 `access`）

```jsonc
"access": {
  "read": true,
  "grant": "read",              // 免确认直达的最高级：read | read+data | read+schema | read+destructive | admin
  "write_allow": ["sales.*"],   // 可选：给了就只允许这些目标(库.表 / collection / key 前缀，glob)可写
  "write_deny":  ["sales.audit"],// 黑名单，命中必拒，优先于白名单
  "confirm_above": null,        // 超过该级需确认；默认=grant
  "allow_escalation": false     // true：允许用一次性确认令牌越权到 T3（破坏性）
}
```

从只读到全权的阶梯（`grant`）：

| grant | 免确认可达 | 典型用途 |
|---|---|---|
| `read` | T0 | **默认**，agent 只读 |
| `read+data` | ≤T1 | 允许常规数据写 |
| `read+schema` | ≤T2 | 允许建表/改结构 |
| `read+destructive` | ≤T3 | 允许 DROP 等（高危，慎用） |
| `admin` | ≤T4 | 理论最高；但 ADMIN 级命令仍被 decide 硬拒 |

## 决策逻辑（`mcp_server/guard.decide`）

```
allow_ceiling = min(grant_max, confirm_above)
level == READ       -> 放行(若 read)，否则拒绝
level == ADMIN      -> 拒绝（永久）
目标命中 write_deny -> 拒绝
给了 write_allow 且目标不在其中 -> 拒绝
level <= allow_ceiling            -> 放行
level <= T3 且 allow_escalation   -> 需要确认（返回确认令牌）
否则                              -> 拒绝（超出授权）
```

## 确认流（agent 二次调用）

需要确认时，工具**不执行**，返回：

```json
{ "requires_confirmation": true, "op":"execute", "risk_level":"DESTRUCTIVE",
  "target":"orders", "intent":"…超出免确认上限…",
  "confirm_token":"<exp>.<hmac>", "next":"确认后用相同参数再调一次并附 confirm=…" }
```

令牌 = `HMAC(secret, source|op|level|target|exp)`，默认 300 秒过期（`DB_CONFIRM_TTL`），**绑定具体操作**：换个表/命令旧令牌无效（会再次要求确认）。agent 把它呈现给用户，用户同意后带 `confirm="<token>"` 用相同参数重发即执行。密钥用 `DB_CONFIRM_SECRET`（不设则每进程随机，重启即作废）。

## 权限档与多环境（插件写一次，环境各配权限）

授权对象是**连接目标（source）本身**——一个 source 就是"某主机:端口/某库"的一个具体连接，没有角色概念。

- **插件按方言只写一次**（`connectors/mysql.py` 等）。
- **同一方言可挂多个环境**，各是一个 source、各带各的 host/port/账号/库，也各挂各的权限。例：本机 MySQL 8.0(生产) 与 5.7(测试)、docker 里的 8.0，都用同一个 mysql 插件，但权限不同。
- **权限档 profile** 让同类环境共享一套权限（缓存一档、业务库一档），个别环境引用档后再内联微调：

```jsonc
// env DB_ACCESS_PROFILES
{"prod":{"grant":"read","allow_escalation":true},
 "sandbox":{"grant":"read+destructive"},
 "cache":{"grant":"read+data"}}

// env DB_SOURCES：access 可为 档名字符串 / 内联 dict / {"profile":"..", 覆盖..}
[
 {"name":"mysql8-prod","dialect":"mysql","port":3306,"database":"biz","user":"app_ro","access":"prod"},
 {"name":"mysql57-sandbox","dialect":"mysql","port":3307,"database":"legacy","access":"sandbox"},
 {"name":"mysql8-audit","dialect":"mysql","port":3306,"database":"biz",
  "access":{"profile":"prod","write_deny":["audit_log"]}}   // 沿用 prod 档 + 本环境再禁写 audit_log
]
```

解析优先级：内联字段 > profile。`source` 名要能区分环境（含实例/端口/用途），MCP 工具用 `source` 参数路由到对应环境与权限。

> 权限档可不写进 env：把字典放进仓库里的 `access_profiles.json`，用 `DB_ACCESS_PROFILE_FILE`（绝对或相对 cwd 路径）引用；若同时给了 `DB_ACCESS_PROFILES`，内联项覆盖同名文件档，便于临时调整。缺文件/未知档名一律回落到最安全的 `readonly`。

## 向后兼容

无 `access` 时：
- `allow_write: false`（默认）→ 等价 `grant=read` 且不可升级（写直接拒）。
- `allow_write: true` → 等价 `grant=read+destructive` 且不需确认（旧"给写就全放开"行为）。

## 审计

读、写、被拒、需确认、已确认执行，均按双边界（mcp + connector）落 JSONL 审计，见 `audit.md`。
