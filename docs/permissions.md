# 读写分离与分级授权

设计目标：agent 默认只能读；写按"库白/黑名单 + 操作分级 + 免确认等级"控制；超过免确认上限的操作，用一次性确认令牌放行（带操作意图）。策略只在 MCP 使用层（`mcp_server`）实施，连接器本身能力全开。

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

## 向后兼容

无 `access` 时：
- `allow_write: false`（默认）→ 等价 `grant=read` 且不可升级（写直接拒）。
- `allow_write: true` → 等价 `grant=read+destructive` 且不需确认（旧"给写就全放开"行为）。

## 审计

读、写、被拒、需确认、已确认执行，均按双边界（mcp + connector）落 JSONL 审计，见 `audit.md`。
