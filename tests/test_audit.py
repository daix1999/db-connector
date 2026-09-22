"""审计测试：核心脱敏 + MCP 层记录 + 连接器层(库直调)自动留痕。"""
from __future__ import annotations
import json, os, sys, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dbconnector import audit  # noqa: E402
from dbconnector.base import BaseConnector  # noqa: E402
from dbconnector.config import ConnectorConfig  # noqa: E402


def _env(log=None, params="", layer="all", master="on"):
    os.environ["DB_AUDIT"] = master
    os.environ["DB_AUDIT_PARAMS"] = params
    os.environ["DB_AUDIT_LAYER"] = layer
    if log:
        os.environ["DB_AUDIT_LOG"] = log
    audit.reset()


def _tmp_log():
    return os.path.join(tempfile.mkdtemp(), "audit.jsonl")


def _lines(log):
    return [json.loads(l) for l in open(log, encoding="utf-8").read().splitlines() if l.strip()]


def test_shape_masks_values():
    s = audit._shape({"sku": "ThinkPad", "qty": 2})
    assert s["keys"] == ["sku", "qty"] and "ThinkPad" not in json.dumps(s, ensure_ascii=False)
    assert audit._shape([1, 2, 3]) == {"$type": "array", "len": 3}


def test_mcp_layer_ok_and_mask():
    log = _tmp_log(); _env(log, params="")
    @audit.audited("query", layer="mcp")
    def query(sql, params=None, source=None):
        return {"rowcount": 2, "executed_sql": sql}
    query(sql="SELECT * FROM t WHERE x=%s", params=["机密值"], source="mysql")
    rec = _lines(log)[-1]
    assert rec["layer"] == "mcp" and rec["op"] == "query" and rec["outcome"] == "ok"
    assert rec["detail"] == {"rowcount": 2}
    assert "机密值" not in json.dumps(rec, ensure_ascii=False)     # 参数值脱敏
    assert rec["args"]["sql"].startswith("SELECT")                # SQL 留痕


def test_mcp_layer_denied():
    log = _tmp_log(); _env(log)
    @audit.audited("execute", layer="mcp")
    def execute(sql, source=None):
        raise PermissionError("只读")
    try:
        execute(sql="DELETE FROM t", source="mysql")
    except PermissionError:
        pass
    rec = _lines(log)[-1]
    assert rec["outcome"] == "denied" and "只读" in rec["error"]


def test_log_params_on():
    log = _tmp_log(); _env(log, params="1")
    @audit.audited("query", layer="mcp")
    def query(sql, params=None):
        return {"rowcount": 1}
    query(sql="SELECT 1", params=["v1"])
    assert "v1" in json.dumps(_lines(log)[-1], ensure_ascii=False)


def test_disable_writes_nothing():
    log = _tmp_log(); _env(log, master="off")
    @audit.audited("query", layer="mcp")
    def query(sql):
        return {}
    query(sql="SELECT 1")
    assert not os.path.exists(log)


# ---- 连接器层：库直调自动留痕（不经过 MCP）----
class _Stub(BaseConnector):
    dialect = "_stub"
    data_model = "test"
    AUDITED_OPS = ("do", "boom")

    def ensure_ready(self): pass
    def ping(self): return True
    def list_sources(self): from dbconnector.result import Result; return Result.values([])
    def describe_source(self, name): from dbconnector.result import Result; return Result.kv({})
    def get_source(self, name, limit=20): from dbconnector.result import Result; return Result.values([])
    def do(self, x, params=None): return {"rowcount": 1}
    def boom(self): raise ValueError("拒绝")


def test_connector_layer_autowrap():
    log = _tmp_log(); _env(log, params="")
    s = _Stub(ConnectorConfig(dialect="_stub", host="127.0.0.1", database="d", label="inst1"))
    s.do(1, params=["机密"])
    try:
        s.boom()
    except ValueError:
        pass
    recs = _lines(log)
    ok = [r for r in recs if r["op"] == "do"][0]
    bad = [r for r in recs if r["op"] == "boom"][0]
    assert ok["layer"] == "connector" and ok["outcome"] == "ok" and ok["detail"] == {"rowcount": 1}
    assert ok.get("label") == "inst1" and ok.get("dialect") == "_stub"
    assert "机密" not in json.dumps(ok, ensure_ascii=False)
    assert bad["outcome"] == "denied" and "拒绝" in bad["error"]


def test_double_wrap_prevented():
    # do 只应被包一层：一次调用只产一条记录
    log = _tmp_log(); _env(log)
    s = _Stub(ConnectorConfig(dialect="_stub"))
    s.do(1)
    assert len([r for r in _lines(log) if r["op"] == "do"]) == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个审计测试全部通过 ✅")
