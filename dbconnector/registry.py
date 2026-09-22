"""方言注册表 + 工厂 + 插件发现。

可插拔的落点：新数据库只要 @register("dialect") 即被工厂发现；也支持外部 pip 包通过
entry_points 组 `dbconnector.dialects` 注册方言，底座无需改代码。
"""
from __future__ import annotations

from typing import Type

from .base import BaseConnector
from .config import ConnectorConfig
from .exceptions import DialectNotRegisteredError

_REGISTRY: dict[str, Type[BaseConnector]] = {}


def register(dialect: str):
    """类装饰器：把某个方言实现登记到指定方言名下。"""
    def _deco(cls: Type[BaseConnector]):
        if not issubclass(cls, BaseConnector):
            raise TypeError(f"{cls.__name__} 必须继承 BaseConnector 才能注册")
        cls.dialect = dialect
        _REGISTRY[dialect] = cls
        return cls
    return _deco


def _load_plugins() -> None:
    """首次使用时：加载内置 connectors + 外部 entry_points 方言插件（幂等、容错）。"""
    if getattr(_load_plugins, "_done", False):
        return
    _load_plugins._done = True
    try:  # 内置
        from . import connectors  # noqa: F401
    except Exception:  # pragma: no cover
        pass
    try:  # 外部 pip 插件：entry_points group = dbconnector.dialects
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="dbconnector.dialects") if hasattr(eps, "select") \
            else eps.get("dbconnector.dialects", [])
        for ep in group:
            try:
                ep.load()
            except Exception:  # pragma: no cover
                pass
    except Exception:  # pragma: no cover
        pass


def available_dialects() -> list[str]:
    _load_plugins()
    return sorted(_REGISTRY)


def get_class(dialect: str) -> Type[BaseConnector]:
    _load_plugins()
    if dialect not in _REGISTRY:
        raise DialectNotRegisteredError(dialect, available_dialects())
    return _REGISTRY[dialect]


def family_of(dialect: str) -> str:
    _load_plugins()
    cls = _REGISTRY.get(dialect)
    return getattr(cls, "data_model", "unknown") if cls else "unknown"


def dialect_info() -> list[dict]:
    """供 agent/MCP 自描述：每个方言属于哪个数据模型族。"""
    _load_plugins()
    return [{"dialect": d, "family": getattr(c, "data_model", "unknown"),
             "default_port": c.default_port} for d, c in sorted(_REGISTRY.items())]


def create(config: ConnectorConfig) -> BaseConnector:
    cls = get_class(config.dialect)
    return cls(config)


def connect(dialect: str, **kwargs) -> BaseConnector:
    cfg = ConnectorConfig.from_kwargs(dialect, **kwargs)
    return create(cfg)
