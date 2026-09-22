"""MongoDB 连接器（基于 pymongo）—— 纯方言插件，只提供连接 + 驱动原语。

通用探查（list/describe/get_source）与派生操作（count/delete）都在 DocumentConnector 模板里
基于下列原语实现。插件负责：建连、ping、close，以及把各文档操作映射到 pymongo。
"""
from __future__ import annotations

from typing import Any

from ..config import ConnectorConfig
from ..exceptions import ConnectionError_
from ..registry import register
from ..result import Result
from ..templates.document import DocumentConnector


def _to_jsonable(v: Any) -> Any:
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
    if hasattr(v, "isoformat"):
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
                        kw.update(username=cfg.user, password=cfg.password)
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

    # ---- 驱动原语 ----
    def list_collection_names(self) -> list[str]:
        return list(self.db.list_collection_names())

    def find(self, collection, filter=None, *, projection=None, sort=None,
             limit=100, skip=0) -> list[dict[str, Any]]:
        cur = self.db[collection].find(filter or {}, projection=projection)
        if sort:
            cur = cur.sort(sort)
        if skip:
            cur = cur.skip(skip)
        return [_to_jsonable(d) for d in cur.limit(int(limit))]

    def insert_one(self, collection, document) -> Any:
        return str(self.db[collection].insert_one(document).inserted_id)

    def insert_many(self, collection, documents) -> list:
        return [str(x) for x in self.db[collection].insert_many(documents).inserted_ids]

    def update_one(self, collection, filter, update) -> int:
        return self.db[collection].update_one(filter, update).modified_count

    def delete_many(self, collection, filter) -> int:
        return self.db[collection].delete_many(filter or {}).deleted_count

    def count_documents(self, collection, filter=None) -> int:
        return self.db[collection].count_documents(filter or {})

    def aggregate(self, collection, pipeline, allow_disk_use: bool = False) -> list[dict]:
        return [_to_jsonable(d) for d in
                self.db[collection].aggregate(pipeline, allowDiskUse=allow_disk_use)]

    # describe_source 用到的额外原语
    def index_information(self, collection) -> list[dict]:
        return [{"name": k, "key": _to_jsonable(v)} for k, v in self.db[collection].index_information().items()]

    def estimated_document_count(self, collection) -> int:
        try:
            return self.db[collection].estimated_document_count()
        except Exception:
            return self.db[collection].count_documents({})

    # ---- 额外能力（非模板要求）----
    def command(self, name: str, *args: Any) -> Any:
        return _to_jsonable(self.db.command(name, *args))

    def list_databases(self) -> Result:
        return Result.values(sorted(self.client.list_database_names()))

    def health_check(self) -> dict[str, Any]:
        base = super().health_check()
        base["database"] = self.db_name
        return base
