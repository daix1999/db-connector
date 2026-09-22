"""ops_review 复盘工具离线测试：按风险级/判定/源过滤 + 聚合。"""
from __future__ import annotations
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import ops_review as R   # noqa: E402


def _mk(records):
    p = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def _d(source, op, level, decision, ts="2026-09-22T10:00:00"):
    return {"layer": "decision", "ts": ts, "label": source, "dialect": "mysql",
            "op": op, "target": "t", "level": level, "decision": decision,
            "why": "x", "grant": "WRITE_DATA", "confirm_from": "WRITE_DATA"}


SAMPLE = [
    _d("prod", "execute", "WRITE_DATA", "allow"),
    _d("prod", "execute", "WRITE_DATA", "confirm"),
    _d("sandbox", "execute", "WRITE_SCHEMA", "allow"),
    _d("sandbox", "execute", "DESTRUCTIVE", "allow"),
    _d("cache", "command", "DESTRUCTIVE", "deny"),
    _d("cache", "command", "ADMIN", "deny"),
]


def test_filter_exact_level():
    recs = R.load(_mk(SAMPLE))
    got = R.apply_filters(recs, level=R._to_level("WRITE_DATA"))
    assert len(got) == 2 and all(r["level"] == "WRITE_DATA" for r in got)


def test_filter_min_level():
    recs = R.load(_mk(SAMPLE))
    got = R.apply_filters(recs, min_level=R._to_level("DESTRUCTIVE"))
    assert {r["level"] for r in got} == {"DESTRUCTIVE", "ADMIN"} and len(got) == 3


def test_level_numeric():
    recs = R.load(_mk(SAMPLE))
    assert len(R.apply_filters(recs, level=4)) == 1        # ADMIN
    assert R._to_level("WRITE_DATA") == 1


def test_filter_decision_and_source():
    recs = R.load(_mk(SAMPLE))
    assert len(R.apply_filters(recs, decision="deny")) == 2
    assert len(R.apply_filters(recs, source="prod")) == 2
    assert len(R.apply_filters(recs, source="prod", decision="allow")) == 1


def test_summary_and_timeline_run():
    recs = R.load(_mk(SAMPLE))
    s = R.summarize(recs)
    assert "按风险级" in s and "DESTRUCTIVE" in s
    t = R.timeline(recs, 30)
    assert "sandbox" in t and "WRITE_DATA" in t


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个 ops_review 复盘测试全部通过 ✅")
