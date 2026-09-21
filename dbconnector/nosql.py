"""NoSQL 族中间基类：约定"键值"与"文档"两类数据模型的通用操作面。

连接器能力全开（读+写），只读限制交给使用层（MCP guard）。
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from .base import BaseConnector


class KeyValueConnector(BaseConnector):
    """键值型（Redis 等）。"""

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
        """执行任意原生命令（使用层负责白名单/只读裁剪）。"""


class DocumentConnector(BaseConnector):
    """文档型（MongoDB 等）。"""

    @abstractmethod
    def find(self, collection: str, filter: dict | None = None, *,
             projection: dict | None = None, sort: list | None = None,
             limit: int = 100, skip: int = 0) -> list[dict[str, Any]]: ...

    @abstractmethod
    def insert_one(self, collection: str, document: dict) -> Any: ...

    @abstractmethod
    def insert_many(self, collection: str, documents: list[dict]) -> list: ...

    @abstractmethod
    def update_one(self, collection: str, filter: dict, update: dict) -> int: ...

    @abstractmethod
    def delete(self, collection: str, filter: dict) -> int: ...

    @abstractmethod
    def count(self, collection: str, filter: dict | None = None) -> int: ...
