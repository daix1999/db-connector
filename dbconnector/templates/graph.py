"""图数据模型模板（Neo4j / NebulaGraph / JanusGraph / Amazon Neptune）。

面向 agent 的统一动作：以查询语言（Cypher/GQL 等）执行图查询 + 邻居遍历。
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector


class GraphConnector(BaseConnector):
    data_model = "graph"
    #: 查询语言标识，供上层标注：cypher | ngql | gremlin ...
    query_language = ""

    @abstractmethod
    def run_query(self, query: str, params: dict | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def neighbors(self, node_id: str, *, rel: str | None = None, depth: int = 1,
                  limit: int = 100) -> list[dict[str, Any]]: ...

    @abstractmethod
    def schema(self) -> dict[str, Any]:
        """返回节点标签 / 关系类型，供 agent 了解图结构。"""

    def list_sources(self):
        from ..result import Result
        return Result.kv(self.schema())

    def describe_source(self, name: str):
        from ..result import Result
        return Result.kv({"label": name, **self.schema().get("labels", {}).get(name, {})})

    def get_source(self, name: str, limit: int = 20):
        raise NotImplementedError(f"{self.dialect} 需覆盖 get_source 以按标签取样本节点")
