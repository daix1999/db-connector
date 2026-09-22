# Changelog

面向 agent 的可插拔数据库连接器 MCP。遵循语义化版本。

> 版本说明：项目早期以多版本号迭代开发，最终形态统一以 **1.0.0** 作为首个正式基线发布；开发期的版本号与 release 已废弃清除。

## 1.0.0 — 首个正式基线

为 AI Agent 设计的可插拔数据库连接器，以 stdio MCP server 提供；评判标准是"agent 调用顺手"。

架构
- 三层：`BaseConnector`（跨方言统一契约）→ `templates/<数据模型>`（原生能力→通用探查映射）→ `connectors/<方言>`（只填原语的插件）。
- 已实现方言：MySQL / Redis / MongoDB；另含 Columnar/Search/Graph/TimeSeries/Vector 模板。
- 插件发现：内置 `@register` + 第三方包 entry_points（组 `dbconnector.dialects`），加新库底座零改动。

MCP server
- 单进程多源：`DB_SOURCES`(JSON)，工具用 `source` 参数路由；族专属工具校验目标源方言。
- agent 优先：`sources` 自描述、跨方言统一探查、错误可据以决策、返回带 `next/hint`、结果限流。

安全与权限（在使用层，连接器能力全开）
- 读写分离分级授权：操作客观分 5 级（READ/WRITE_DATA/WRITE_SCHEMA/DESTRUCTIVE/ADMIN）。
- 每环境 `access` 两旋钮 `grant`(硬上限)+`confirm_from`(确认起点)+库/表黑白名单；超阈值走一次性 HMAC 确认令牌（绑定操作、默认 300s、不可复用）；ADMIN 恒拒。
- 权限档 profile 可复用（`DB_ACCESS_PROFILE_FILE`/`DB_ACCESS_PROFILES`）；授权对象=连接 source、无角色。

可追溯
- 双边界审计（connector + mcp）：库直调与经 MCP 操作都留痕，默认脱敏、不记凭据。
- 写操作无条件落 `layer=decision`（放行也记判定与依据）；事务块内逐条语句审计。

工具链
- `analyze`（MCP 工具 + `scripts/ops_analyze.py`）：执行前静态决策预演，与执行同源、不连库、不发令牌。
- `ops_review.py`：审计复盘，按风险级/源/判定过滤、`--group-by`、`--html` 报表；默认只看 decision。

质量与文档
- 大量离线单测（注册/分级/授权决策/审计/配置/分析/复盘）+ 真实 MySQL/Redis/Mongo 端到端（确认流、多环境权限、事务）。
- 向后兼容：`DBConnector` 别名、`nosql` 垫片、`allow_write` 与 `allow_escalation/confirm_above` 旧配置自动映射。
- README 总纲 + docs/{architecture,templates-and-plugins,permissions,audit,configuration,faq}.md。
