"""MCP server 配置：多源 DB_SOURCES（推荐）或单方言老变量（兼容）。

每个源除连接参数外，带一个 `access` 权限块（读写分离分级授权）：
    "access": {
      "read": true,                       # 读是否放行（默认 true）
      "grant": "read",                    # 免确认直达的最高操作级：read|read+data|read+schema|read+destructive|admin
      "write_allow": ["sales.*"],         # 写白名单(可选)：给了就只允许这些目标(库/表/collection/key前缀 glob)
      "write_deny":  ["sales.audit"],     # 写黑名单：命中必拒，优先于白名单
      "confirm_above": null,              # 超过该级需二次确认(默认=grant)；配合 allow_escalation 可越权确认
      "allow_escalation": false           # true 时：超过 grant(≤破坏性) 的操作可用一次性确认令牌放行
    }

向后兼容：无 access 时，`allow_write:true` 视为 grant=read+destructive 且不需确认；false 视为 grant=read 且不可升级。
全局兜底：DB_ALLOW_WRITE / DB_MAX_ROWS。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dbconnector import ConnectorConfig, PoolConfig
from dbconnector import levels
from dbconnector.acl import Access

_TRUE = {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _truthy(v, default: bool) -> bool:
    return default if v is None else str(v).lower() in _TRUE


@dataclass
class Source:
    name: str
    config: ConnectorConfig
    access: Access
    max_rows: int

    @property
    def allow_write(self) -> bool:        # 兼容旧字段读用
        return self.access.grant_max >= levels.WRITE_DATA


@dataclass
class ServerSettings:
    sources: dict[str, Source] = field(default_factory=dict)

    def names(self) -> list[str]:
        return list(self.sources)

    def default_name(self) -> str | None:
        if len(self.sources) == 1:
            return next(iter(self.sources))
        return None


def _parse_access(item: dict, g_allow: bool) -> Access:
    a = item.get("access")
    if isinstance(a, dict):
        return Access.from_dict(a)
    return Access.from_legacy(_truthy(item.get("allow_write"), g_allow))


def _global_defaults() -> tuple[bool, int]:
    return _truthy(_env("DB_ALLOW_WRITE"), False), int(_env("DB_MAX_ROWS", "200"))


def _build_source(item: dict, g_allow: bool, g_max: int) -> Source:
    dialect = item["dialect"]
    port = item.get("port")
    pool_kwargs = {k: v for k, v in item.items()
                   if k in ("mincached", "maxcached", "maxconnections")}
    cfg = ConnectorConfig(
        dialect=dialect, label=item.get("name", dialect),
        host=item.get("host", "127.0.0.1"), port=int(port) if port else None,
        user=item.get("user"), password=item.get("password"),
        database=item.get("database"), dsn=item.get("dsn"),
        pool=PoolConfig(**pool_kwargs) if pool_kwargs else PoolConfig(mincached=1, maxcached=4, maxconnections=8),
        extra=item.get("extra", {}),
    )
    return Source(name=item.get("name", dialect), config=cfg,
                  access=_parse_access(item, g_allow), max_rows=int(item.get("max_rows", g_max)))


def load_settings() -> ServerSettings:
    g_allow, g_max = _global_defaults()
    raw = _env("DB_SOURCES")
    if raw:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = [items]
        srcs = {}
        for it in items:
            s = _build_source(it, g_allow, g_max)
            srcs[s.name] = s
        return ServerSettings(sources=srcs)

    single = {"dialect": _env("DB_DIALECT", "mysql"), "host": _env("DB_HOST", "127.0.0.1"),
              "port": _env("DB_PORT"), "user": _env("DB_USER"), "password": _env("DB_PASSWORD"),
              "database": _env("DB_DATABASE"), "dsn": _env("DB_DSN")}
    single = {k: v for k, v in single.items() if v not in (None, "")}
    dialect = single.get("dialect", "mysql")
    s = _build_source({"name": dialect, **single}, g_allow, g_max)
    return ServerSettings(sources={dialect: s})
