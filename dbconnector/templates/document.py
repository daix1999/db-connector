"""文档数据模型模板（MongoDB / CouchDB / DynamoDB 等）。

插件实现一组"驱动原语"；通用探查（list/describe/get_source）与派生操作（count/delete）
由模板基于原语统一实现，插件不必各写一份。
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector
from ..result import Result


class DocumentConnector(BaseConnector):
    data_model = "document"
    AUDITED_OPS = ("find", "insert_one", "insert_many", "update_one", "delete",
                   "count", "aggregate", "list_sources", "describe_source", "get_source")

    # ---- 驱动原语（插件实现）----
    @abstractmethod
    def list_collection_names(self) -> list[str]: ...
    @abstractmethod
    def find(self, collection: str, filter: dict | None = None, *, projection=None,
             sort=None, limit: int = 100, skip: int = 0) -> list[dict[str, Any]]: ...
    @abstractmethod
    def insert_one(self, collection: str, document: dict) -> Any: ...
    @abstractmethod
    def insert_many(self, collection: str, documents: list[dict]) -> list: ...
    @abstractmethod
    def update_one(self, collection: str, filter: dict, update: dict) -> int: ...
    @abstractmethod
    def delete_many(self, collection: str, filter: dict) -> int: ...
    @abstractmethod
    def count_documents(self, collection: str, filter: dict | None = None) -> int: ...
    @abstractmethod
    def aggregate(self, collection: str, pipeline: list) -> list[dict[str, Any]]: ...

    # ---- 派生操作（模板统一实现）----
    def delete(self, collection: str, filter: dict) -> int:
        return self.delete_many(collection, filter or {})

    def count(self, collection: str, filter: dict | None = None) -> int:
        return self.count_documents(collection, filter)

    # ---- 通用探查契约（模板统一实现）----
    def list_sources(self) -> Result:
        return Result.values(sorted(self.list_collection_names()))

    def describe_source(self, name: str) -> Result:
        idx = getattr(self, "index_information", lambda c: [])(name)
        est = getattr(self, "estimated_document_count", lambda c: None)(name)
        return Result.kv({"collection": name, "count_estimate": est, "indexes": idx})

    def get_source(self, name: str, limit: int = 20) -> Result:
        return Result.docs(self.find(name, {}, limit=limit))
