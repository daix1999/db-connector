"""文档数据模型模板（MongoDB / CouchDB / DynamoDB 等）。"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector


class DocumentConnector(BaseConnector):
    """文档型模板。连接器本身能力全开，只读裁剪在使用层。"""

    data_model = "document"

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

    def aggregate(self, collection: str, pipeline: list) -> list[dict[str, Any]]:
        raise NotImplementedError(f"{self.dialect} 未实现 aggregate")
