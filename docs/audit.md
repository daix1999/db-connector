# 操作审计（双边界）

审计在两个边界各记一份，共用一个 logger（一把锁写同一 JSONL 文件，并发不串行交错）：

- **connector 层**（`dbconnector/audit.py`）：下沉到 `BaseConnector`。无论经 MCP 还是直接 `connect(...).query(...)`，落库操作都留痕。`AUDITED_OPS` 里的方法被自动包装；事务块内 `execute/query/executemany` 逐条记录，收尾补一条汇总（提交=ok / 回滚=error + 语句与写次数）。
- **mcp 层**（`mcp_server/audit.py` 复用核心）：每个工具调用记一次，含被权限护栏拒绝/要求确认的调用（这类不会到达 connector 层）。

## 记录字段

`ts, layer(mcp|connector), source/label, dialect, data_model, op, args(脱敏), outcome(ok|denied|confirm|error), dur_ms, detail(结果量), error`。

## 脱敏

- 保留：SQL 文本（截断）、标识符（表名/collection/key）。
- `params/payload/filter/documents/args` 等含业务值的参数，默认只记形状（键名/长度），不记值。
- 连接凭据永不记录；审计写失败不影响主调用。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DB_AUDIT` | on | 总开关（off/0/false 关闭） |
| `DB_AUDIT_LAYER` | all | all / connector / mcp / off |
| `DB_AUDIT_LOG` | `logs/db-connector-audit.jsonl` | 路径；该目录已 gitignore |
| `DB_AUDIT_PARAMS` | 关 | 1=记录参数值（敏感） |

## 一次典型写操作的记录

直接库调用：`connector execute INSERT → ok affected_rows=1`。
经 MCP：先 `mcp execute INSERT`（放行），同时 `connector execute`；若触发确认，则只有 `mcp execute → confirm`（未到 connector），带令牌重发后 `mcp → ok` + `connector → ok`。
