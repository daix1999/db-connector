"""Redis 连接器（基于 redis-py）—— 纯方言插件，只提供连接 + 原始命令原语。

所有读/写/扫描/类型感知探查逻辑都在 KeyValueConnector 模板里用 execute_command 统一实现。
本插件只负责：建连接、ping、close、把 execute_command 透传给 redis-py。
"""
from __future__ import annotations

from typing import Any

from ..config import ConnectorConfig
from ..exceptions import ConnectionError_
from ..registry import register
from ..templates.keyvalue import KeyValueConnector


@register("redis")
class RedisConnector(KeyValueConnector):
    dialect = "redis"
    default_port = 6379

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._client = None

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
                        password=cfg.password, db=self._db_number(), decode_responses=True,
                        socket_connect_timeout=5, socket_timeout=5, **cfg.extra)
            except Exception as e:
                raise ConnectionError_(f"初始化 Redis 连接池失败: {e}") from e
            self._client = redis.Redis(connection_pool=pool)
        return self._client

    def _db_number(self) -> int:
        try:
            return int(self.config.database) if self.config.database not in (None, "") else 0
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

    # 唯一原语：原始命令透传（返回已 decode）
    def execute_command(self, *args: Any) -> Any:
        return self.client.execute_command(*args)

    def health_check(self) -> dict[str, Any]:
        base = super().health_check()
        base["db"] = self._db_number()
        return base
