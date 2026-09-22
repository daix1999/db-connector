"""数据模型模板层（第二层）。

一种数据模型 = 一个模板 = 一类"agent 交互契约"。新增数据库时，先选它属于哪个模板，
再去 connectors/ 写一个继承该模板的方言插件即可。

  模板                数据模型      代表数据库
  RelationalConnector 关系(SQL)     MySQL/PostgreSQL/SQL Server/Oracle/SQLite/MariaDB/TiDB
  ColumnarConnector   列式/OLAP     ClickHouse/Doris/StarRocks/Druid/Greenplum
  KeyValueConnector   键值          Redis/Memcached/etcd
  DocumentConnector   文档          MongoDB/CouchDB/DynamoDB
  SearchConnector     检索          Elasticsearch/OpenSearch/Solr
  GraphConnector      图            Neo4j/NebulaGraph/JanusGraph/Neptune
  TimeSeriesConnector 时序          InfluxDB/QuestDB/TimescaleDB/TDengine
  VectorConnector     向量          Milvus/Qdrant/Weaviate/Chroma/pgvector
"""
from __future__ import annotations

from .columnar import ColumnarConnector
from .document import DocumentConnector
from .graph import GraphConnector
from .keyvalue import KeyValueConnector
from .relational import DBConnector, RelationalConnector
from .search import SearchConnector
from .timeseries import TimeSeriesConnector
from .vector import VectorConnector

#: data_model -> 模板类，供插件与上层按族发现
FAMILIES: dict[str, type] = {
    "relational": RelationalConnector,
    "columnar": ColumnarConnector,
    "keyvalue": KeyValueConnector,
    "document": DocumentConnector,
    "search": SearchConnector,
    "graph": GraphConnector,
    "timeseries": TimeSeriesConnector,
    "vector": VectorConnector,
}

__all__ = [
    "RelationalConnector", "DBConnector", "ColumnarConnector", "KeyValueConnector",
    "DocumentConnector", "SearchConnector", "GraphConnector", "TimeSeriesConnector",
    "VectorConnector", "FAMILIES",
]
