"""方言注册表 + 工厂。

新增一种数据库只需：写好子类并加 @register("dialect")，工厂会自动发现并实例化，
无需修改任何调用方代码 —— 这就是"可插拔"的落点。
"""
from __future__ import annotations

from typing import Type

from .base import BaseConnector, DBConnector
from .config import ConnectorConfig
from .exceptions import DialectNotRegisteredError

_REGISTRY: dict[str, Type[BaseConnector]] = {}


def register(dialect: str):
    """类装饰器：把某个连接器实现登记到指定方言名下。"""
    def _deco(cls: Type[BaseConnector]):
        if not issubclass(cls, BaseConnector):
            raise TypeError(f"{cls.__name__} 必须继承 BaseConnector 才能注册")
        cls.dialect = dialect
        _REGISTRY[dialect] = cls
        return cls
    return _deco


def available_dialects() -> list[str]:
    _load_builtin_connectors()
    return sorted(_REGISTRY)


def get_class(dialect: str) -> Type[BaseConnector]:
    _load_builtin_connectors()
    if dialect not in _REGISTRY:
        raise DialectNotRegisteredError(dialect, available_dialects())
    return _REGISTRY[dialect]


def create(config: ConnectorConfig) -> BaseConnector:
    """根据已构建的 ConnectorConfig 实例化对应连接器。"""
    cls = get_class(config.dialect)
    return cls(config)


def connect(dialect: str, **kwargs) -> BaseConnector:
    """最常用的入口：方言 + 连接参数 -> 连接器实例（惰性建连）。

    例：connect("mysql", host=..., user=...) / connect("redis", host=...)
    """
    cfg = ConnectorConfig.from_kwargs(dialect, **kwargs)
    return create(cfg)


_loaded = False


def _load_builtin_connectors() -> None:
    """首次使用时导入内置连接器子模块，触发 @register。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:  # pragma: no cover
        from . import connectors  # noqa: F401
    except Exception:
        pass
