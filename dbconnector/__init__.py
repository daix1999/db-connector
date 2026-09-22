"""dbconnector —— 面向 agent 的可插拔数据库连接器底座（两层抽象·多数据模型）。

三层结构：
    第一层 base.BaseConnector      —— 跨方言统一契约（生命周期 + list/describe/get_source 探查）
    第二层 templates.<族>          —— 按数据模型的类型模板（关系/列式/键值/文档/检索/图/时序/向量）
    第三层 connectors.<方言>       —— 具体数据库插件，继承某个模板即可接入

新增数据库 = 选一个模板 + 写一个 connectors 插件（内置或用 pip entry_points 外挂），
无需改动底座。连接器能力全开（读写/危险命令都在），只读与安全裁剪在使用层（mcp_server）。

快速上手：
    from dbconnector import connect
    with connect("mysql", host=..., user=..., password=..., database=...) as db:
        db.query("SELECT * FROM t WHERE id=%s", (1,))
"""
from __future__ import annotations

from .base import BaseConnector
from .config import ConnectorConfig, PoolConfig
from .exceptions import (
    ConfigError,
    ConnectionError_,
    ConnectorError,
    DialectNotRegisteredError,
    PoolTimeoutError,
    QueryError,
)
from .registry import (
    available_dialects,
    connect,
    create,
    dialect_info,
    family_of,
    get_class,
    register,
)
from .result import Result, ResultSet
from .templates import (
    FAMILIES,
    ColumnarConnector,
    DBConnector,
    DocumentConnector,
    GraphConnector,
    KeyValueConnector,
    RelationalConnector,
    SearchConnector,
    TimeSeriesConnector,
    VectorConnector,
)

__version__ = "1.1.0"
__all__ = [
    "BaseConnector",
    "RelationalConnector", "ColumnarConnector", "KeyValueConnector", "DocumentConnector",
    "SearchConnector", "GraphConnector", "TimeSeriesConnector", "VectorConnector",
    "DBConnector", "FAMILIES",
    "ConnectorConfig", "PoolConfig", "Result", "ResultSet",
    "connect", "create", "register", "get_class", "available_dialects",
    "dialect_info", "family_of",
    "ConnectorError", "ConfigError", "ConnectionError_", "DialectNotRegisteredError",
    "PoolTimeoutError", "QueryError",
    "__version__",
]
