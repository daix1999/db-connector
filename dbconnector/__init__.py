"""dbconnector —— 可插拔的本地数据库连接器库。

设计：统一的 DBConnector 抽象基类 + 方言注册表/工厂。
MySQL 为首个内置实现；新增 Postgres/SQLServer 等只需 @register 一个子类。

快速上手：
    from dbconnector import connect

    with connect("mysql", host="localhost", user="root",
                 password="xxx", database="test") as db:
        rows = db.query("SELECT * FROM users WHERE id > %s", (0,)).to_list()
        db.execute("UPDATE users SET name=%s WHERE id=%s", ("bob", 1))
        with db.transaction() as tx:
            tx.execute("INSERT INTO users(name) VALUES (%s)", ("alice",))
            tx.execute("INSERT INTO audit(msg) VALUES (%s)", ("created",))
"""
from __future__ import annotations

from .base import BaseConnector, DBConnector, RelationalConnector
from .config import ConnectorConfig, PoolConfig
from .exceptions import (
    ConfigError,
    ConnectionError_,
    ConnectorError,
    DialectNotRegisteredError,
    PoolTimeoutError,
    QueryError,
)
from .nosql import DocumentConnector, KeyValueConnector
from .registry import available_dialects, connect, create, get_class, register
from .result import Result, ResultSet

__version__ = "1.0.0"
__all__ = [
    "BaseConnector",
    "RelationalConnector",
    "DBConnector",          # = RelationalConnector，向后兼容
    "KeyValueConnector",
    "DocumentConnector",
    "ConnectorConfig",
    "PoolConfig",
    "Result",
    "ResultSet",
    "connect",
    "create",
    "register",
    "get_class",
    "available_dialects",
    "ConnectorError",
    "ConfigError",
    "ConnectionError_",
    "DialectNotRegisteredError",
    "PoolTimeoutError",
    "QueryError",
    "__version__",
]
