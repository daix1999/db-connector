"""操作决策分析（analyze）离线测试：纯静态、不连库、不发令牌。"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dbconnector import create, levels                         # noqa: E402
from dbconnector.acl import Access                             # noqa: E402
from dbconnector.config import ConnectorConfig                 # noqa: E402
from mcp_server.config import Source                           # noqa: E402
from mcp_server import analyzer                                # noqa: E402


def _src(dialect, access: Access, name="s"):
    cfg = ConnectorConfig(dialect=dialect, host="h", database="d", label=name)
    return Source(name=name, config=cfg, access=access, max_rows=200)


def _card(dialect, access, op, args):
    conn = create(_src(dialect, access).config)
    return analyzer.build_card(conn, _src(dialect, access), op, args)


def test_read_allowed():
    c = _card("mysql", Access.from_legacy(False), "query", {"sql": "SELECT 1"})
    assert c["risk"]["level_name"] == "READ" and c["authorize"]["decision"] == "allow"


def test_write_on_readonly_denied_and_flagged():
    c = _card("mysql", Access.from_legacy(False), "execute", {"sql": "UPDATE t SET x=1"})
    assert c["risk"]["level_name"] == "DESTRUCTIVE"          # 无 WHERE 升破坏性
    assert c["authorize"]["decision"] == "deny"
    assert any("WHERE" in s for s in c["safety_notes"])
    assert c["preview"]["where_present"] is False


def test_confirm_needed_and_no_token():
    acc = Access.from_dict({"grant": "read+destructive", "confirm_from": "read+data"})
    c = _card("mysql", acc, "execute", {"sql": "UPDATE t SET x=1 WHERE id=2"})
    assert c["authorize"]["decision"] == "confirm"
    assert c["recommendation"] == "needs-human-confirmation"
    blob = str(c)
    assert "confirm_token" not in blob and "token" not in blob.split("note")[0]  # 不签令牌


def test_whitelist_denies_outside():
    acc = Access.from_dict({"grant": "read+data", "write_allow": ["sales_*"]})
    assert _card("mysql", acc, "execute", {"sql": "INSERT INTO user_t VALUES(1)"})["authorize"]["decision"] == "deny"
    assert _card("mysql", acc, "execute", {"sql": "INSERT INTO sales_t VALUES(1)"})["authorize"]["decision"] == "allow"


def test_redis_flow():
    ro = Access.from_legacy(False)
    rw = Access.from_dict({"grant": "read+data"})
    assert _card("redis", ro, "command", {"name": "GET", "args": ["k"]})["authorize"]["decision"] == "allow"
    assert _card("redis", ro, "command", {"name": "SET", "args": ["k", "v"]})["authorize"]["decision"] == "deny"
    assert _card("redis", rw, "command", {"name": "SET", "args": ["k", "v"]})["authorize"]["decision"] == "allow"
    assert _card("redis", rw, "command", {"name": "FLUSHALL", "args": []})["authorize"]["decision"] == "deny"


def test_mongo_flow():
    ro = Access.from_legacy(False)
    rw = Access.from_dict({"grant": "read+data"})
    assert _card("mongodb", ro, "find", {"collection": "c"})["authorize"]["decision"] == "allow"
    assert _card("mongodb", ro, "insert_one", {"collection": "c"})["authorize"]["decision"] == "deny"
    assert _card("mongodb", rw, "insert_one", {"collection": "c"})["authorize"]["decision"] == "allow"
    agg = _card("mongodb", rw, "aggregate", {"collection": "c", "pipeline": [{"$out": "x"}]})
    assert agg["risk"]["level_name"] == "DESTRUCTIVE"


def test_normalize_input():
    assert analyzer.normalize_input("s", sql="SELECT 1")[0] == "query"
    assert analyzer.normalize_input("s", sql="DELETE FROM t WHERE 1")[0] == "execute"
    assert analyzer.normalize_input("s", command="GET")[0] == "command"
    assert analyzer.normalize_input("s", collection="c", mongo_op="insert")[0] == "insert_one"


def test_decision_brief_and_allowed_write_is_recorded():
    import json, os, tempfile
    from dbconnector import audit
    for k in ("DB_AUDIT", "DB_AUDIT_LAYER"):
        os.environ.pop(k, None)
    log = os.path.join(tempfile.mkdtemp(), "a.jsonl")
    os.environ["DB_AUDIT"] = "on"; os.environ["DB_AUDIT_LOG"] = log; os.environ["DB_AUDIT_LAYER"] = "all"
    audit.reset()

    # grant=read+data 环境：INSERT 是"放行"，仍要记一条 decision
    conn = create(_src("mysql", Access.from_dict({"grant": "read+data"})).config)
    src = _src("mysql", Access.from_dict({"grant": "read+data"}))
    brief = analyzer.decision_brief(conn, src, "execute", {"sql": "INSERT INTO t VALUES(1)"})
    assert brief["decision"] == "allow" and brief["level"] == "WRITE_DATA"
    audit.record_decision(conn, brief)

    recs = [json.loads(l) for l in open(log, encoding="utf-8").read().splitlines() if l.strip()]
    dec = [r for r in recs if r["layer"] == "decision"]
    assert len(dec) == 1 and dec[0]["decision"] == "allow" and dec[0]["op"] == "execute"
    assert dec[0]["read_only"] is False
    audit.reset()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个 analyze 决策分析测试全部通过 ✅")
