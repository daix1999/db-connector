"""检索数据模型模板（Elasticsearch / OpenSearch / Solr / Meilisearch）。

面向 agent 的统一动作：按 query 字符串或 DSL 检索 + 聚合，屏蔽 _index/_doc 术语差异。
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector


class SearchConnector(BaseConnector):
    data_model = "search"

    @abstractmethod
    def list_indexes(self) -> list[str]: ...

    @abstractmethod
    def search(self, index: str, query: str | dict, size: int = 20) -> dict[str, Any]:
        """query 可为纯文本（模板转 match）或原生 DSL dict。返回 hits/total/aggs。"""

    @abstractmethod
    def index_doc(self, index: str, document: dict, doc_id: str | None = None) -> Any: ...

    @abstractmethod
    def delete_doc(self, index: str, doc_id: str) -> Any: ...

    def list_sources(self):
        from ..result import Result
        return Result.values(self.list_indexes())

    def describe_source(self, name: str):
        raise NotImplementedError(f"{self.dialect} 需覆盖 describe_source 以返回 mapping")

    def get_source(self, name: str, limit: int = 20):
        from ..result import Result
        r = self.search(name, {"match_all": {}}, size=limit)
        return Result.docs([h.get("_source", h) for h in r.get("hits", [])])
