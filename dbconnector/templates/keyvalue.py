"""键值数据模型模板（Redis / Memcached / etcd 等）。

设计：模板用**一个原语** `execute_command(*args)`（原始命令透传）实现全部读/写/探查，
插件只需提供连接与该原语即可，无需各自再写 list/describe/get_source —— 这类映射归模板。
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector
from ..result import Result


class KeyValueConnector(BaseConnector):
    data_model = "keyvalue"
    AUDITED_OPS = ("get", "set", "delete", "exists", "scan", "command",
                   "list_sources", "describe_source", "get_source")

    # 唯一必须由插件实现的原语
    @abstractmethod
    def execute_command(self, *args: Any) -> Any:
        ...

    # ---- 以下均由 execute_command 统一实现，插件不必重写 ----
    def command(self, name: str, *args: Any) -> Any:
        return self.execute_command(name, *args)

    def get(self, key: str) -> Any:
        return self.execute_command("GET", key)

    def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        args = ["SET", key, value] + (["EX", int(ttl)] if ttl else [])
        return bool(self.execute_command(*args))

    def delete(self, *keys: str) -> int:
        return int(self.execute_command("DEL", *keys)) if keys else 0

    def exists(self, key: str) -> bool:
        return bool(self.execute_command("EXISTS", key))

    def type_of(self, key: str) -> str:
        return self.execute_command("TYPE", key)

    def scan(self, match: str = "*", count: int = 100) -> list[str]:
        cursor, collected = 0, []
        for _ in range(50):  # 最多 50 轮，防大库无限扫
            cursor, keys = self.execute_command("SCAN", cursor, "MATCH", match, "COUNT", count)
            collected += list(keys or [])
            if int(cursor) == 0:
                break
        return collected

    # ---- 通用探查契约 ----
    def list_sources(self) -> Result:
        keys = sorted(self.scan(match="*", count=200))
        dbsize = self.execute_command("DBSIZE")
        return Result.values(keys, dbsize=int(dbsize) if dbsize is not None else None,
                             note="scan 采样(最多 50 轮)")

    def describe_source(self, name: str) -> Result:
        return Result.kv({"key": name, "type": self.type_of(name),
                          "ttl": self.execute_command("TTL", name), "exists": self.exists(name)})

    def get_source(self, name: str, limit: int = 20) -> Result:
        t = self.type_of(name)
        if t == "string":
            return Result.single(self.execute_command("GET", name))
        if t == "hash":
            return Result.kv(self._as_dict(self.execute_command("HGETALL", name)))
        if t == "list":
            return Result.values(self._as_list(self.execute_command("LRANGE", name, 0, limit - 1)))
        if t == "set":
            return Result.values(self._as_list(self.execute_command("SMEMBERS", name)))
        if t == "zset":
            return Result.kv(self._as_dict(self.execute_command("ZRANGE", name, 0, limit - 1, "WITHSCORES")))
        return Result.kv({"key": name, "type": t, "exists": self.exists(name)})

    @staticmethod
    def _as_dict(raw: Any) -> dict[str, Any]:
        """兼容 redis-py 三种返回：dict / [(member,score)…] 元组对 / 扁平 [k,v,k,v…]。"""
        if isinstance(raw, dict):
            return {str(k): v for k, v in raw.items()}
        seq = list(raw or [])
        if seq and isinstance(seq[0], (list, tuple)):
            return {str(p[0]): p[1] for p in seq}
        return {str(seq[i]): seq[i + 1] for i in range(0, len(seq) - 1, 2)}

    @staticmethod
    def _as_list(raw: Any) -> list[Any]:
        if isinstance(raw, (set, frozenset)):
            return sorted(raw)
        return list(raw or [])
