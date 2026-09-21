"""MySQL 连接器实现（基于 PyMySQL + DBUtils 连接池）。"""
from __future__ import annotations

from typing import Any

from ..base import RelationalConnector
from ..registry import register


@register("mysql")
class MySQLConnector(RelationalConnector):
    """MySQL / MariaDB 连接器。

    依赖 PyMySQL（纯 Python，免装编译型驱动）。默认 utf8mb4 字符集、
    字典游标、超时与自动重连可按需通过 extra 覆盖。
    """

    dialect = "mysql"
    default_port = 3306

    @property
    def dbapi(self):
        try:
            import pymysql
        except ImportError as e:  # pragma: no cover
            raise ImportError("MySQL 连接器需要 PyMySQL：pip install pymysql") from e
        return pymysql

    def _creator_name(self) -> str:
        # PooledDB 用 pymysql.Connection 作为创建函数
        return "Connection"

    def _connect_kwargs(self) -> dict[str, Any]:
        import pymysql
        cfg = self.config
        kwargs: dict[str, Any] = {
            "host": cfg.host,
            "port": cfg.port or self.default_port,
            "user": cfg.user,
            "password": cfg.password,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
            "autocommit": False,   # 由基类统一控制 commit/rollback
            "connect_timeout": 10,
        }
        if cfg.database:
            kwargs["database"] = cfg.database
        kwargs.update(cfg.extra)  # 允许用户覆盖任何默认项
        return kwargs
