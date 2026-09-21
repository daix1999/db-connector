"""NoSQL 只读护栏离线测试（不依赖 Redis/Mongo 服务）。"""
from __future__ import annotations
import os, sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server import guard  # noqa: E402


def test_redis_read_commands_allowed():
    for c in ["get", "GET", "hgetall", "scan", "ttl", "smembers", "info", "dbsize"]:
        assert guard.redis_read_only_ok(c), f"应允许: {c}"


def test_redis_write_commands_denied():
    for c in ["set", "del", "expire", "flushall", "config", "lpush", "hset", "shutdown"]:
        assert not guard.redis_read_only_ok(c), f"应拒绝写: {c}"


def test_mongo_read_ops():
    assert guard.mongo_read_only_ok("find")
    assert guard.mongo_read_only_ok("count")
    assert not guard.mongo_read_only_ok("insert_one")
    assert not guard.mongo_read_only_ok("delete")


def test_mongo_pipeline_read_only():
    assert guard.mongo_pipeline_read_only([{"$match": {"a": 1}}, {"$group": {"_id": "$a"}}])
    assert not guard.mongo_pipeline_read_only([{"$out": "other"}])
    assert not guard.mongo_pipeline_read_only([{"$merge": {"into": "x"}}])
    assert not guard.mongo_pipeline_read_only("not-a-list")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个 NoSQL 护栏测试全部通过 ✅")
