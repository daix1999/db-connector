"""审计日志离线测试：脱敏 + 记录成功/拒绝/参数值开关。"""
from __future__ import annotations
import importlib, json, os, sys, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload_audit(env):
    for k, v in env.items():
        os.environ[k] = v
    import mcp_server.audit as a
    importlib.reload(a)
    return a


def test_shape_masks_values():
    a = _reload_audit({"DB_AUDIT": "on", "DB_AUDIT_PARAMS": ""})
    s = a._shape({"sku": "ThinkPad", "qty": 2})
    assert s["keys"] == ["sku", "qty"] and "ThinkPad" not in json.dumps(s)
    assert a._shape([1, 2, 3]) == {"$type": "array", "len": 3}


def test_audited_records_ok_and_masks_params():
    log = os.path.join(tempfile.mkdtemp(), "a.jsonl")
    a = _reload_audit({"DB_AUDIT": "on", "DB_AUDIT_LOG": log, "DB_AUDIT_PARAMS": ""})

    @a.audited("query")
    def query(sql, params=None, source=None):
        return {"rowcount": 2, "executed_sql": sql}

    query(sql="SELECT * FROM t WHERE x=%s", params=["secret-value"], source="mysql")
    rec = json.loads(open(log, encoding="utf-8").read().strip())
    assert rec["tool"] == "query" and rec["outcome"] == "ok" and rec["source"] == "mysql"
    assert rec["detail"] == {"rowcount": 2}
    assert "secret-value" not in json.dumps(rec, ensure_ascii=False)      # 参数值被脱敏
    assert rec["args"]["params"]["$type"] == "array"
    assert rec["args"]["sql"].startswith("SELECT")                        # SQL 文本保留


def test_audited_records_denied():
    log = os.path.join(tempfile.mkdtemp(), "a.jsonl")
    a = _reload_audit({"DB_AUDIT": "on", "DB_AUDIT_LOG": log})

    class ToolError(Exception): pass
    @a.audited("execute")
    def execute(sql, source=None):
        raise ToolError("源只读")
    try:
        execute(sql="DELETE FROM t", source="mysql")
    except ToolError:
        pass
    rec = json.loads(open(log, encoding="utf-8").read().strip())
    assert rec["outcome"] == "denied" and "只读" in rec["error"]


def test_log_params_on_includes_values():
    log = os.path.join(tempfile.mkdtemp(), "a.jsonl")
    a = _reload_audit({"DB_AUDIT": "on", "DB_AUDIT_LOG": log, "DB_AUDIT_PARAMS": "1"})

    @a.audited("query")
    def query(sql, params=None, source=None):
        return {"rowcount": 1}
    query(sql="SELECT 1", params=["v1"])
    rec = json.loads(open(log, encoding="utf-8").read().strip())
    assert "v1" in json.dumps(rec, ensure_ascii=False)


def test_disable_audit_writes_nothing():
    log = os.path.join(tempfile.mkdtemp(), "a.jsonl")
    a = _reload_audit({"DB_AUDIT": "off", "DB_AUDIT_LOG": log})

    @a.audited("query")
    def query(sql, source=None):
        return {}
    query(sql="SELECT 1")
    assert not os.path.exists(log)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个审计测试全部通过 ✅")
