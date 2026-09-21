"""统一异常层级：所有连接器抛出的错误都继承自 ConnectorError，方便上层捕获。"""
from __future__ import annotations


class ConnectorError(Exception):
    """连接器基础异常。"""


class ConfigError(ConnectorError):
    """配置缺失或非法。"""


class DialectNotRegisteredError(ConnectorError):
    """请求了未注册的数据库方言（dialect）。"""

    def __init__(self, dialect: str, available: list[str] | None = None):
        self.dialect = dialect
        self.available = available or []
        hint = f" 已注册方言: {', '.join(self.available)}" if self.available else " 目前没有任何方言注册。"
        super().__init__(f"未注册的数据库方言: '{dialect}'。{hint}")


class ConnectionError_(ConnectorError):
    """建立物理连接失败。"""


class QueryError(ConnectorError):
    """SQL 执行 / 查询出错，保留原始 DBAPI 异常。"""

    def __init__(self, message: str, sql: str | None = None, cause: BaseException | None = None):
        self.sql = sql
        self.cause = cause
        full = message if sql is None else f"{message} | SQL: {sql!r}"
        super().__init__(full)


class PoolTimeoutError(ConnectorError):
    """从连接池获取连接超时。"""
