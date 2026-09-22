"""MCP server 配置：支持"单方言(向后兼容)"与"多方言多源"两种。

多源（推荐）：env DB_SOURCES 为 JSON 数组，每项：
    {"name":"mysql","dialect":"mysql","host":"127.0.0.1","port":3306,
     "user":"root","password":"...","database":"test","allow_write":false,"max_rows":200}
    {"name":"cache","dialect":"redis","host":"127.0.0.1","port":6379,"database":"0"}
    {"name":"docs","dialect":"mongodb","host":"127.0.0.1","port":27017,"database":"app"}

单方言（旧）：DB_DIALECT/DB_HOST/... 仍可用，自动命名为该方言名。

全局默认：DB_ALLOW_WRITE / DB_MAX_ROWS 作为未在源里显式设置时的兜底。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dbconnector import ConnectorConfig, PoolConfig

_TRUE = {"1", "true", "yes", "y", "on"}


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    return v if v not in (None, "") else default


def _truthy(v, default: bool) -> bool:
    if v is None:
        return default
    return str(v).lower() in _TRUE


@dataclass
class Source:
    name: str
    config: ConnectorConfig
    allow_write: bool
    max_rows: int


@dataclass
class ServerSettings:
    sources: dict[str, Source] = field(default_factory=dict)

    def names(self) -> list[str]:
        return list(self.sources)

    def default_name(self) -> str | None:
        if len(self.sources) == 1:
            return next(iter(self.sources))
        return None


def _global_defaults() -> tuple[bool, int]:
    allow = _truthy(_env("DB_ALLOW_WRITE"), False)
    maxrows = int(_env("DB_MAX_ROWS", "200"))
    return allow, maxrows


def _build_source(item: dict, g_allow: bool, g_max: int) -> Source:
    dialect = item["dialect"]
    port = item.get("port")
    pool_kwargs = {k: v for k, v in item.items()
                   if k in ("mincached", "maxcached", "maxconnections")}
    cfg = ConnectorConfig(
        dialect=dialect,
        label=item.get("name", dialect),
        host=item.get("host", "127.0.0.1"),
        port=int(port) if port else None,
        user=item.get("user"),
        password=item.get("password"),
        database=item.get("database"),
        dsn=item.get("dsn"),
        pool=PoolConfig(**pool_kwargs) if pool_kwargs else PoolConfig(mincached=1, maxcached=4, maxconnections=8),
        extra=item.get("extra", {}),
    )
    return Source(
        name=item.get("name", dialect),
        config=cfg,
        allow_write=_truthy(item.get("allow_write"), g_allow),
        max_rows=int(item.get("max_rows", g_max)),
    )


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

    # 向后兼容：单方言老变量
    single = {
        "dialect": _env("DB_DIALECT", "mysql"),
        "host": _env("DB_HOST", "127.0.0.1"),
        "port": _env("DB_PORT"),
        "user": _env("DB_USER"),
        "password": _env("DB_PASSWORD"),
        "database": _env("DB_DATABASE"),
        "dsn": _env("DB_DSN"),
    }
    single = {k: v for k, v in single.items() if v not in (None, "")}
    s = _build_source({"dialect": single.get("dialect", "mysql"),
                       **{k: v for k, v in single.items() if k != "dialect"}}, g_allow, g_max)
    # 单方言模式默认名沿用旧行为：source 名 = 方言名
    return ServerSettings(sources={single.get("dialect", "mysql"): s})
