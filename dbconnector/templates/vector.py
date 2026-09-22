"""向量数据模型模板（Milvus / Qdrant / Weaviate / Chroma / pgvector / Elasticsearch-kNN）。

面向 agent 的核心动作是"按语义取 top_k 相似"，与 SQL/文档差异很大，故独立成模板：
统一 search_by_vector / upsert / 集合管理，屏蔽各家 SDK 差异。
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector


class VectorConnector(BaseConnector):
    data_model = "vector"

    #: 距离度量：cosine | l2 | ip（各库命名不同，模板统一抽象）
    metrics = ("cosine", "l2", "ip")

    @abstractmethod
    def list_collections(self) -> list[str]: ...

    @abstractmethod
    def describe_collection(self, name: str) -> dict[str, Any]:
        """返回 dim / metric / index 类型等。"""

    @abstractmethod
    def create_collection(self, name: str, dim: int, metric: str = "cosine") -> Any: ...

    @abstractmethod
    def drop_collection(self, name: str) -> Any: ...

    @abstractmethod
    def upsert(self, collection: str, vectors: list[list[float]],
               ids: list | None = None, payloads: list[dict] | None = None) -> Any: ...

    @abstractmethod
    def search_by_vector(self, collection: str, vector: list[float], top_k: int = 10,
                         filter: dict | None = None) -> list[dict[str, Any]]:
        """返回 [{id, score, payload}]，供 agent 直接消费。"""

    # ---- 通用探查契约映射到"集合" ----
    def list_sources(self):
        from ..result import Result
        return Result.values(self.list_collections())

    def describe_source(self, name: str):
        from ..result import Result
        return Result.kv(self.describe_collection(name))

    def get_source(self, name: str, limit: int = 20):
        from ..result import Result
        return Result.kv({"collection": name, **self.describe_collection(name)})
