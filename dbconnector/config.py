"""连接器配置对象：统一承载连接参数 + 池参数，支持从 kwargs / 环境变量 / dict 构建。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class PoolConfig:
    """连接池参数（DBUtils.PooledDB 语义）。"""

    mincached: int = 1        # 启动时预先创建的连接数
    maxcached: int = 5        # 空闲连接最多保留数
    maxconnections: int = 10  # 池内最大连接数，0 表示不限
    blocking: bool = True     # 池满时是否阻塞等待（False 则抛 TooManyConnections）
    max_usage: int = 0        # 单条连接最大复用次数，0 表示不限
    ping: int = 1             # 取连接时是否 ping 检测（1=每次，4=出错时）


@dataclass
class ConnectorConfig:
    """方言无关的连接配置。

    - host / port / user / password / database：通用连接参数
    - dsn：某些方言（如 SQLite 文件路径）可直接用 dsn 覆盖
    - pool：连接池参数
    - extra：透传给底层 DBAPI 的额外参数（如 charset、autocommit 等）
    """

    dialect: str
    host: str = "localhost"
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None
    dsn: str | None = None
    #: 审计/展示用的可读名（如 MCP 源名），可选
    label: str | None = None
    pool: PoolConfig = field(default_factory=PoolConfig)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_kwargs(cls, dialect: str, **kwargs: Any) -> "ConnectorConfig":
        """从关键字参数构建，未知参数落入 extra。"""
        known = {"host", "port", "user", "password", "database", "dsn", "label"}
        core = {k: kwargs.pop(k) for k in list(kwargs) if k in known}
        pool_kwargs = {k: kwargs.pop(k) for k in list(kwargs) if k in _POOL_FIELDS}
        extra_kwargs = kwargs  # 剩余全部透传
        pool = PoolConfig(**pool_kwargs) if pool_kwargs else PoolConfig()
        return cls(dialect=dialect, pool=pool, extra=extra_kwargs, **core)

    @classmethod
    def from_env(cls, dialect: str, prefix: str = "DB_") -> "ConnectorConfig":
        """从环境变量构建：DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_DATABASE。"""
        def _get(name: str) -> str | None:
            return os.getenv(prefix + name) or None

        port_raw = _get("PORT")
        return cls.from_kwargs(
            dialect=dialect,
            host=_get("HOST") or "localhost",
            port=int(port_raw) if port_raw else None,
            user=_get("USER"),
            password=_get("PASSWORD"),
            database=_get("DATABASE"),
        )


_POOL_FIELDS = {f for f in PoolConfig.__dataclass_fields__}
