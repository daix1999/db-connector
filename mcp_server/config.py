"""MCP server 配置：多源 DB_SOURCES（推荐）或单方言老变量（兼容）。

插件按方言只写一次；一个进程可挂任意多个"环境(source)"，多个 source 可同属一个方言。
授权对象是"连接目标(source)"本身，不设角色。权限既可按源内联，也可引用可复用的"权限档 profile"
（缓存归缓存、业务库归业务库共享一档，个别环境再内联微调）。

env DB_ACCESS_PROFILES（JSON dict，档名 -> access）：
    {"prod":{"grant":"read","allow_escalation":true},
     "sandbox":{"grant":"read+destructive"},
     "cache":{"grant":"read+data"}}
env DB_SOURCES（JSON 数组），每项 access 可为 档名字符串 / dict / {"profile":"prod",..覆盖..}：
    {"name":"mysql8-prod","dialect":"mysql","host":"127.0.0.1","port":3306,"user":"app",
     "password":"...","database":"biz","access":"prod","max_rows":200}
    {"name":"mysql57-test","dialect":"mysql","host":"127.0.0.1","port":3307,"user":"root",
     "password":"...","database":"legacy","access":"sandbox"}
    {"name":"audit","dialect":"mysql",...,"access":{"profile":"prod","write_deny":["audit_log"]}}

access 细则见 dbconnector/acl.py 与 docs/permissions.md。全局兜底 DB_ALLOW_WRITE / DB_MAX_ROWS。
向后兼容：无 access 时 allow_write=true ≈ grant=read+destructive 免确认；false ≈ grant=read。
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


def _load_profiles() -> dict:
    """全局权限档：env DB_ACCESS_PROFILES（JSON dict，档名 -> access 定义）。"""
    raw = _env("DB_ACCESS_PROFILES")
    return json.loads(raw) if raw else {}


def _resolve_access_dict(item: dict, profiles: dict) -> dict | None:
    """把 source 的 access 解析成有效 dict：
       - access 为字符串   → 引用权限档 profile
       - access 为 dict 且含 "profile" → 档 + 内联覆盖(内联优先)
       - access 为普通 dict → 内联
       - 无 access          → None（走 allow_write 兼容）
    """
    a = item.get("access")
    if isinstance(a, str):
        return dict(profiles.get(a, {}))
    if isinstance(a, dict):
        base: dict = {}
        if "profile" in a:
            base = dict(profiles.get(a["profile"], {}))
            a = {k: v for k, v in a.items() if k != "profile"}
        base.update(a)   # 内联覆盖 profile
        return base
    return None


def _parse_access(item: dict, profiles: dict, g_allow: bool) -> Access:
    d = _resolve_access_dict(item, profiles)
    if d is not None:
        return Access.from_dict(d)
    return Access.from_legacy(_truthy(item.get("allow_write"), g_allow))


def _global_defaults() -> tuple[bool, int]:
    return _truthy(_env("DB_ALLOW_WRITE"), False), int(_env("DB_MAX_ROWS", "200"))


def _build_source(item: dict, profiles: dict, g_allow: bool, g_max: int) -> Source:
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
                  access=_parse_access(item, profiles, g_allow), max_rows=int(item.get("max_rows", g_max)))


def load_settings() -> ServerSettings:
    g_allow, g_max = _global_defaults()
    profiles = _load_profiles()
    raw = _env("DB_SOURCES")
    if raw:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = [items]
        srcs = {}
        for it in items:
            s = _build_source(it, profiles, g_allow, g_max)
            srcs[s.name] = s
        return ServerSettings(sources=srcs)

    single = {"dialect": _env("DB_DIALECT", "mysql"), "host": _env("DB_HOST", "127.0.0.1"),
              "port": _env("DB_PORT"), "user": _env("DB_USER"), "password": _env("DB_PASSWORD"),
              "database": _env("DB_DATABASE"), "dsn": _env("DB_DSN")}
    single = {k: v for k, v in single.items() if v not in (None, "")}
    dialect = single.get("dialect", "mysql")
    s = _build_source({"name": dialect, **single}, profiles, g_allow, g_max)
    return ServerSettings(sources={dialect: s})
