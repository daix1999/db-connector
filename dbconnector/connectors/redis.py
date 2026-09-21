"""Redis 连接器（基于 redis-py）。

能力全开：读(get/scan/hgetall/lrange…)、写(set/delete/expire…)、原生 command(含危险命令)。
只读裁剪不在这里做，由使用层(MCP guard)按运行时开关限制。

依赖：redis>=5.0（redis-py）。
"""
from __future__ import annotations

from typing import Any

from ..config import ConnectorConfig
from ..exceptions import ConnectionError_
from ..nosql import KeyValueConnector
from ..registry import register
from ..result import Result


@register("redis")
class RedisConnector(KeyValueConnector):
    dialect = "redis"
    default_port = 6379

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._client = None

    # ---- 连接 ----
    @property
    def client(self):
        if self._client is None:
            try:
                import redis
            except ImportError as e:  # pragma: no cover
                raise ConnectionError_("Redis 连接器需要 redis-py：pip install redis") from e
            cfg = self.config
            try:
                if cfg.dsn:
                    pool = redis.ConnectionPool.from_url(cfg.dsn, decode_responses=True)
                else:
                    pool = redis.ConnectionPool(
                        host=cfg.host, port=cfg.port or self.default_port,
                        password=cfg.password, db=self._db_number(),
                        decode_responses=True, **cfg.extra)
            except Exception as e:
                raise ConnectionError_(f"初始化 Redis 连接池失败: {e}") from e
            self._client = redis.Redis(connection_pool=pool)
        return self._client

    def _db_number(self) -> int:
        db = self.config.database
        try:
            return int(db) if db not in (None, "") else 0
        except (TypeError, ValueError):
            return 0

    def ensure_ready(self) -> None:
        self.ping()

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.connection_pool.disconnect()
            finally:
                self._client = None

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    # ---- 键值原语 ----
    def get(self, key: str) -> Any:
        return self.client.get(key)

    def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        if ttl:
            return bool(self.client.set(key, value, ex=ttl))
        return bool(self.client.set(key, value))

    def delete(self, *keys: str) -> int:
        return int(self.client.delete(*keys)) if keys else 0

    def exists(self, key: str) -> bool:
        return bool(self.client.exists(key))

    def scan(self, match: str = "*", count: int = 100) -> list[str]:
        cur, keys = self.client.scan(cursor=0, match=match, count=count)
        return list(keys)

    def command(self, name: str, *args: Any) -> Any:
        fn = getattr(self.client, name.lower(), None)
        if fn is None or name.startswith("_"):
            raise ValueError(f"不支持的 Redis 命令: {name!r}")
        return fn(*args)

    # ---- 通用探查契约 ----
    def list_sources(self) -> Result:
        keys = self.scan(match="*", count=200)
        dbsize = self.client.dbsize()
        return Result.values(sorted(keys), dbsize=dbsize, note="scan 采样(最多200)")

    def describe_source(self, name: str) -> Result:
        t = self.client.type(name)
        return Result.kv({"key": name, "type": t, "ttl": self.client.ttl(name),
                          "exists": self.exists(name)})

    def get_source(self, name: str, limit: int = 20) -> Result:
        """按 key 的类型读取若干成员（string/hash/list/set/zset）。"""
        t = self.client.type(name)
        if t == "string":
            return Result.single(self.client.get(name))
        if t == "hash":
            return Result.kv(self.client.hgetall(name))
        if t == "list":
            return Result.values(self.client.lrange(name, 0, limit - 1))
        if t == "set":
            return Result.values(list(self.client.sscan_iter(name, count=limit)))
        if t == "zset":
            return Result.kv({m: s for m, s in self.client.zscan(name, count=limit)[0]})
        return Result.kv({"key": name, "type": t, "exists": self.exists(name)})

    # ---- 额外便利 ----
    def info(self, section: str | None = None) -> Result:
        data = self.client.info(section) if section else self.client.info()
        return Result.kv({str(k): str(v) for k, v in data.items()})

    def health_check(self) -> dict[str, Any]:
        base = super().health_check()
        base["db"] = self._db_number()
        return base
