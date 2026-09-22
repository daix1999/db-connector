# Changelog

面向 agent 的可插拔数据库连接器 MCP。所有 notable 变更记于此。遵循语义化版本。

## 2.6.2 — 文档优化
- README 收为总纲：修正示例为两旋钮 `grant`+`confirm_from`、新增环境变量一览表、命令行工具表、FAQ 节选与索引。
- 新增 `docs/configuration.md`（环境变量/`access` 字段/完整多环境示例）、`docs/faq.md`（FAQ + 排障）。
- `.env.example` 升级为多源 + 权限档 + 审计/令牌（保留单方言兼容注释）。
- `docs/templates-and-plugins.md` 补"新插件自动继承 授权/决策审计/复盘/多源路由"。
- 新增本 `CHANGELOG.md`。纯文档，无功能变更。

## 2.6.1 — 复盘默认作用域
- `ops_review` 默认只看 `layer=decision` 写决策记录（`scope_default`），不被连接器读执行行淹没；`--layer mcp|connector` 可放宽。

## 2.6.0 — 复盘增强
- `ops_review` 加 `--group-by`（level/source/op/dialect/family/decision/layer）与 `--html` 自包含报表（风险级配色、分组、明细）。

## 2.5.0 — 操作复盘 CLI
- 新增 `scripts/ops_review.py`：读审计 JSONL，按风险级/源/判定/层/时间过滤 + 聚合 + 时间线。

## 2.4.0 — 决策分析与决策审计
- 新增 `mcp_server/analyzer.py`（静态决策卡，与执行同源、不连库、不发令牌）+ MCP 工具 `analyze` + `scripts/ops_analyze.py`。
- 写操作无条件落 `layer=decision` 审计（放行也记录判定与依据）。
- 修复 `classify_sql` 把 `UPDATE … SET` 误判为 ADMIN 的 bug（分级改为按语句首关键词）。

## 2.3.0 — 权限模型收敛
- `Access` 由 6 字段/3 旋钮 收敛为两旋钮 `grant`(硬上限)+`confirm_from`(确认起点)；去掉 `allow_escalation`/`confirm_above`（旧配置自动映射兼容）。

## 2.2.x — 权限档 profile
- 2.2.0 `DB_ACCESS_PROFILES` 权限档 + 同一插件多环境各配权限（授权对象=连接 source，无角色）。
- 2.2.1 权限档可外置 `access_profiles.json`（`DB_ACCESS_PROFILE_FILE` 引用，内联覆盖文件）。

## 2.1.0 — 分级授权下沉到根
- `dbconnector/acl.py`（`Access`+`decide`）+ `BaseConnector.authorize = classify→decide`；各模板实现 `classify`；MCP 只管令牌。所有模板共用一套流程。

## 2.0.0 — 模板收口 + 读写分离分级授权 + 分层文档
- 类型模板分层（`templates/`）、entry_points 插件发现；插件只填原语，通用探查归模板。
- 读写分离分级授权（levels 五级 + 每源 `access` + 一次性确认令牌）。
- `docs/` 分层文档，根 README 总纲。

## 1.x — 基线
- 1.0.0 稳定基线：两层抽象连接器（MySQL/Redis/Mongo）+ stdio MCP server + 测试/冒烟。
- 1.1.0 类型模板分层雏形、MCP 多源、agent 自描述增强。
- 1.2.0 使用层操作审计。1.3.0 审计下沉到 `BaseConnector`（库直调也留痕）。1.3.1 事务块内逐条语句审计。
