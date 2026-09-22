# 配置与环境变量

一切运行期配置通过环境变量注入 MCP server 进程（stdio）。凭据只进环境，别提交进仓库（`.env`、`logs/` 已在 `.gitignore`）。

## 两种配置方式

1. 多源（推荐）：`DB_SOURCES` 传一个 JSON 数组，一个进程同时挂多个环境（可同方言不同权限）。
2. 单方言（向后兼容）：只给 `DB_DIALECT/DB_HOST/...`，自动生成一个名为该方言的源。

## 环境变量总表

### 数据源
| 变量 | 说明 |
|---|---|
| `DB_SOURCES` | JSON 数组，每项 = 一个连接环境：`name/dialect/host/port/user/password/database/dsn/access/max_rows/extra` 等 |
| `DB_DIALECT` `DB_HOST` `DB_PORT` `DB_USER` `DB_PASSWORD` `DB_DATABASE` `DB_DSN` | 单方言老写法（`DB_SOURCES` 缺省时生效） |
| `DB_MAX_ROWS` | 全局单次行数上限（SQL 查询无 LIMIT 时按此补），默认 200 |
| `DB_ALLOW_WRITE` | 全局兜底写权限（无 `access` 时：false=只读，true=到破坏性且免确认） |

### 权限档 profile（可复用）
| 变量 | 说明 |
|---|---|
| `DB_ACCESS_PROFILE_FILE` | 指向一个 JSON 文件（如仓库内 `access_profiles.json`），档名→access 定义；支持绝对或相对 cwd 路径 |
| `DB_ACCESS_PROFILES` | 内联 JSON；与文件同名档时**内联覆盖文件**，便于临时调整 |

source 的 `access` 三态：`"档名"` ｜ `{内联 dict}` ｜ `{"profile":"档名", ...覆盖字段}`（内联优先）。缺文件/未知档名 → 回落最安全 `readonly`。

### 审计
| 变量 | 默认 | 说明 |
|---|---|---|
| `DB_AUDIT` | `on` | 总开关；`off/0/false` 关闭 |
| `DB_AUDIT_LAYER` | `all` | `all` \| `connector` \| `mcp` \| `off`（`decision` 记录随 mcp 层） |
| `DB_AUDIT_LOG` | `logs/db-connector-audit.jsonl` | 审计文件路径（同用于 `ops_review.py --log` 默认） |
| `DB_AUDIT_PARAMS` | 关 | `1` 时记录参数值（敏感）；默认只记形状 |

### 确认令牌
| 变量 | 默认 | 说明 |
|---|---|---|
| `DB_CONFIRM_SECRET` | 进程随机 | 令牌 HMAC 密钥；不设则重启即令旧令牌失效 |
| `DB_CONFIRM_TTL` | 300 | 令牌有效期（秒） |

## `access` 字段

| 字段 | 含义 |
|---|---|
| `read` | 是否允许读（默认 `true`） |
| `grant` | 该环境免确认/可达的最高操作级：`read`\|`read+data`\|`read+schema`\|`read+destructive`（`admin` 等价 destructive，因 ADMIN 命令恒拒） |
| `confirm_from` | 从哪一级起需一次性确认令牌（默认=`grant`，即到顶也不额外确认） |
| `write_allow` | 写白名单（glob：库.表 / collection / key 前缀）；给了就仅这些目标可写 |
| `write_deny` | 写黑名单，命中必拒，优先于白名单 |
| （旧）`allow_escalation` `confirm_above` `allow_write` | 仍接受，自动翻译成上面两旋钮；老配置无需改 |

判定：`level<confirm_from` 放行 · `confirm_from≤level≤grant` 需确认 · `level>grant` 拒绝；`ADMIN` 恒拒。详见 [permissions.md](permissions.md)。

## 完整示例（可直接粘）

`access_profiles.json`：
```jsonc
{"readonly":{"read":true,"grant":"read"},
 "prod":{"read":true,"grant":"read+destructive","confirm_from":"read+data"},
 "biz-write":{"read":true,"grant":"read+data","write_deny":["audit_log","*_credential"]},
 "cache":{"read":true,"grant":"read+data"},
 "sandbox":{"read":true,"grant":"read+destructive"}}
```

`DB_SOURCES`（多环境、同方言不同权限）：
```jsonc
[
 {"name":"mysql8-prod","dialect":"mysql","host":"127.0.0.1","port":3306,"user":"app_ro","password":"...","database":"biz","access":"prod"},
 {"name":"mysql57-sandbox","dialect":"mysql","host":"127.0.0.1","port":3307,"user":"root","password":"...","database":"legacy","access":"sandbox"},
 {"name":"redis-cache","dialect":"redis","host":"127.0.0.1","port":6379,"database":"0","access":"cache"},
 {"name":"mongo-rpt","dialect":"mongodb","host":"127.0.0.1","port":27017,"database":"analytics","access":"readonly"},
 {"name":"mysql8-ops","dialect":"mysql","host":"127.0.0.1","port":3306,"user":"app","password":"...","database":"biz","access":{"profile":"prod","confirm_from":"read+schema"}}
]
```

## 在客户端注册

`mcp_config.example.json` 里给了两份：`db-connector`（默认只读多环境）与 `db-connector-rw`（写权限梯度）。把 `mcpServers.*` 段合并进 千问办公 / Claude / Cursor 的 MCP 配置，改 `cwd` 与凭据即可。**千问办公不允许 agent 自动注册 stdio MCP，需手动粘贴后重启该连接器。**
