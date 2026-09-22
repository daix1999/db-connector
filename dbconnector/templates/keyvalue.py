"""键值数据模型模板（Redis / Memcached / etcd 等）。"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector


class KeyValueConnector(BaseConnector):
    """键值型模板。连接器本身能力全开，只读裁剪在使用层。"""

    data_model = "keyvalue"

    AUDITED_OPS = ("get", "set", "delete", "exists", "scan", "command",
                   "list_sources", "describe_source", "get_source")

    @abstractmethod
    def get(self, key: str) -> Any: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int | None = None) -> bool: ...

    @abstractmethod
    def delete(self, *keys: str) -> int: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def scan(self, match: str = "*", count: int = 100) -> list[str]:
        """游标式遍历 key（比 KEYS 安全，避免阻塞）。"""

    @abstractmethod
    def command(self, name: str, *args: Any) -> Any:
        """执行任意原生命令（使用层负责白名单/危险命令裁剪）。"""
