# 操作审计（双边界）

审计在两个边界各记一份，共用一个 logger（一把锁写同一 JSONL 文件，并发不串行交错）：

- **connector 层**（`dbconnector/audit.py`）：下沉到 `BaseConnector`。无论经 MCP 还是直接 `connect(...).query(...)`，落库操作都留痕。`AUDITED_OPS` 里的方法被自动包装；事务块内 `execute/query/executemany` 逐条记录，收尾补一条汇总（提交=ok / 回滚=error + 语句与写次数）。
- **mcp 层**（`mcp_server/audit.py` 复用核心）：每个工具调用记一次，含被权限护栏拒绝/要求确认的调用（这类不会到达 connector 层）。

## 记录字段

`ts, layer(mcp|connector), source/label, dialect, data_model, op, args(脱敏), outcome(ok|denied|confirm|error), dur_ms, detail(结果量), error`。

## 操作决策记录（layer=decision，写操作无条件留痕）

Agent 操作的"可追溯"不止是"执行了什么"，还包括"当时怎么判的"。因此每个**写操作（≥数据写级）**在授权点都额外落一条 `layer=decision` 记录，**无论最终放行/需确认/拒绝**：字段含 `op / target / level / decision(allow|confirm|deny) / why / grant / confirm_from`，SQL 还带 `read_only / where_present / multi_statement`，Redis 带 `command`，Mongo 带 `has_out_stage`。

这样即使某次 UPDATE 被 grant 放行，日志里也明确记着"这是一条 WRITE_DATA 操作、在什么目标、依据什么规则放行"——AI 干了什么、系统怎么判的，一查便知。读操作不重复记（连接器层已记其执行）。`analyze` 工具/CLI 复用同一 `classify→decide`，与执行判定一致。

### 复盘 CLI

`scripts/ops_review.py` 读审计 JSONL，按风险级/源/判定/层/时间过滤并聚合成时间线与概览：

```bash
python scripts/ops_review.py                              # 全量概览
python scripts/ops_review.py --min-level WRITE_DATA       # 数据写及以上
python scripts/ops_review.py --level DESTRUCTIVE --source mysql8-prod
python scripts/ops_review.py --decision deny              # 只看被拒
python scripts/ops_review.py --group-by source           # 按环境分组（level|source|op|dialect|family|decision|layer）
python scripts/ops_review.py --min-level WRITE_DATA --html report.html   # 导出自包含 HTML 报表
```
`--level` 精确、`--min-level` 下限，支持级名或数字 0–4；`--group-by` 选分组维度；`--html PATH` 导出带风险级配色、分组汇总与明细表的单文件报表。默认日志路径同 `DB_AUDIT_LOG`。

> 默认作用域 = `decision` 写决策记录（未显式 `--layer` 时），复盘只看"agent 发起的写操作"，不被连接器层的读执行行淹没；要看某一层显式加 `--layer mcp|connector`。

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
