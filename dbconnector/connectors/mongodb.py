"""MongoDB 连接器（基于 pymongo）。

能力全开：读(find/aggregate/count)、写(insert/update/delete)、管理(索引/drop/命令)。
只读裁剪由使用层(MCP guard)按运行时开关限制，不在连接器内写死。

依赖：pymongo>=4.0。
"""
from __future__ import annotations

from typing import Any

from ..config import ConnectorConfig
from ..exceptions import ConnectionError_
from ..nosql import DocumentConnector
from ..registry import register
from ..result import Result


def _to_jsonable(v: Any) -> Any:
    """把 ObjectId / datetime 等转成 JSON 友好值，供上层序列化。"""
    try:
        from bson.objectid import ObjectId
    except Exception:  # pragma: no cover
        ObjectId = ()  # type: ignore
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, dict):
        return {k: _to_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_jsonable(x) for x in v]
    if hasattr(v, "isoformat"):        # datetime / date
        return v.isoformat()
    return v


@register("mongodb")
class MongoConnector(DocumentConnector):
    dialect = "mongodb"
    default_port = 27017

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._client = None

    # ---- 连接 ----
    @property
    def client(self):
        if self._client is None:
            try:
                from pymongo import MongoClient
            except ImportError as e:  # pragma: no cover
                raise ConnectionError_("Mongo 连接器需要 pymongo：pip install pymongo") from e
            cfg = self.config
            try:
                if cfg.dsn:
                    self._client = MongoClient(cfg.dsn, **cfg.extra)
                else:
                    kw = dict(host=cfg.host, port=cfg.port or self.default_port,
                              serverSelectionTimeoutMS=5000, **cfg.extra)
                    if cfg.user:
                        kw["username"] = cfg.user
                        kw["password"] = cfg.password
                    self._client = MongoClient(**kw)
            except Exception as e:
                raise ConnectionError_(f"初始化 MongoDB 客户端失败: {e}") from e
        return self._client

    @property
    def db_name(self) -> str:
        return self.config.database or "test"

    @property
    def db(self):
        return self.client[self.db_name]

    def ensure_ready(self) -> None:
        self.ping()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def ping(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except Exception:
            return False

    # ---- 文档原语 ----
    def find(self, collection, filter=None, *, projection=None, sort=None,
             limit=100, skip=0) -> list[dict[str, Any]]:
        cur = self.db[collection].find(filter or {}, projection=projection)
        if sort:
            cur = cur.sort(sort)
        if skip:
            cur = cur.skip(skip)
        return [_to_jsonable(d) for d in cur.limit(int(limit))]

    def insert_one(self, collection, document) -> Any:
        return self.db[collection].insert_one(document).inserted_id

    def insert_many(self, collection, documents) -> list:
        return list(self.db[collection].insert_many(documents).inserted_ids)

    def update_one(self, collection, filter, update) -> int:
        return self.db[collection].update_one(filter, update).modified_count

    def delete(self, collection, filter) -> int:
        return self.db[collection].delete_many(filter).deleted_count

    def count(self, collection, filter=None) -> int:
        return self.db[collection].count_documents(filter or {})

    def aggregate(self, collection, pipeline, allow_disk_use: bool = False) -> list[dict]:
        return [_to_jsonable(d) for d in
                self.db[collection].aggregate(pipeline, allowDiskUse=allow_disk_use)]

    def command(self, name: str, *args: Any) -> Any:
        return _to_jsonable(self.db.command(name, *args))

    # ---- 通用探查契约 ----
    def list_sources(self) -> Result:
        names = sorted(self.db.list_collection_names())
        return Result.values(names, database=self.db_name)

    def describe_source(self, name: str) -> Result:
        coll = self.db[name]
        idx = [{"name": ix["name"], "keys": list(ix["key"].keys())} for ix in coll.list_indexes()]
        return Result.kv({"collection": name, "count_estimate": coll.estimated_document_count(),
                          "indexes": idx})

    def get_source(self, name: str, limit: int = 20) -> Result:
        docs = self.find(name, limit=limit)
        return Result.docs(docs, collection=name)

    def list_databases(self) -> Result:
        names = sorted(self.client.list_database_names())
        return Result.values(names)

    def health_check(self) -> dict[str, Any]:
        base = super().health_check()
        base["database"] = self.db_name
        return base
