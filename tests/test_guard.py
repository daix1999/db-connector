"""只读护栏离线测试（不依赖数据库）。"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server import guard  # noqa: E402


def test_read_only_allowed():
    for sql in ["SELECT * FROM t", "  select 1 ", "SHOW TABLES",
                "DESC users", "EXPLAIN SELECT * FROM t",
                "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
                "SELECT * FROM t;"]:
        assert guard.is_read_only(sql), f"应判为只读: {sql!r}"


def test_write_rejected():
    for sql in ["INSERT INTO t VALUES(1)", "UPDATE t SET x=1", "DELETE FROM t",
                "DROP TABLE t", "CREATE TABLE t(x int)", "TRUNCATE t",
                "SELECT * FROM t; DELETE FROM t",           # 多语句
                "SELECT * FROM t INTO OUTFILE '/tmp/x'",     # 导出
                "", "CALL p()", "SET @a=1"]:
        assert not guard.is_read_only(sql), f"应拒绝: {sql!r}"


def test_comments_stripped():
    assert guard.is_read_only("-- c\nSELECT 1")
    assert guard.is_read_only("SELECT 1 /* inline */")
    assert not guard.is_read_only("-- SELECT\nDROP TABLE t")


def test_ensure_limit():
    assert guard.ensure_limit("SELECT * FROM t", 100).endswith("LIMIT 100")
    assert "LIMIT 5" in guard.ensure_limit("SELECT * FROM t LIMIT 5", 100)
    assert guard.ensure_limit("SELECT * FROM t LIMIT 5", 100).upper().count("LIMIT") == 1


def test_identifier_guard():
    assert guard.validate_identifier("users") == "users"
    for bad in ["users; DROP", "1abc", "a b", "`x`", "", "a" * 100]:
        try:
            guard.validate_identifier(bad)
            assert False, f"应拒绝标识符: {bad!r}"
        except ValueError:
            pass


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个护栏测试全部通过 ✅")
