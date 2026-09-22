"""根层分级授权流程测试（离线，不连库）：验证所有模板共用 authorize() 一套流程。"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dbconnector import levels                                    # noqa: E402
from dbconnector.acl import Access                                # noqa: E402
from dbconnector.config import ConnectorConfig                    # noqa: E402
from dbconnector.connectors.mysql import MySQLConnector           # noqa: E402
from dbconnector.connectors.redis import RedisConnector           # noqa: E402
from dbconnector.connectors.mongodb import MongoConnector         # noqa: E402


def _read_only():   return Access.from_legacy(False)
def _rw_data():     return Access.from_dict({"grant": "read+data"})
def _rw_esc():      return Access.from_dict({"grant": "read+data", "allow_escalation": True})
def _wl():          return Access.from_dict({"grant": "read+destructive", "write_allow": ["sales*"]})


def test_relational_uses_flow():
    c = MySQLConnector(ConnectorConfig(dialect="mysql"))
    assert c.authorize(_read_only(), "query", {"sql": "SELECT 1"})[0] == "allow"
    assert c.authorize(_read_only(), "execute", {"sql": "INSERT INTO t VALUES(1)"})[0] == "deny"
    assert c.authorize(_rw_data(), "execute", {"sql": "INSERT INTO t VALUES(1)"})[0] == "allow"
    # DROP 破坏性：无升级→deny；可升级→confirm
    assert c.authorize(_rw_data(), "execute", {"sql": "DROP TABLE t"})[0] == "deny"
    assert c.authorize(_rw_esc(), "execute", {"sql": "DROP TABLE t"})[0] == "confirm"
    # 无 WHERE 的 DELETE 升为破坏性
    assert c.classify("execute", {"sql": "DELETE FROM t"})[0] == levels.DESTRUCTIVE
    assert c.classify("execute", {"sql": "DELETE FROM t WHERE id=1"})[0] == levels.WRITE_DATA


def test_keyvalue_uses_same_flow():
    c = RedisConnector(ConnectorConfig(dialect="redis"))
    assert c.authorize(_read_only(), "command", {"name": "GET", "args": ["k"]})[0] == "allow"
    assert c.authorize(_read_only(), "command", {"name": "SET", "args": ["k", "v"]})[0] == "deny"
    assert c.authorize(_rw_data(), "command", {"name": "SET", "args": ["k", "v"]})[0] == "allow"
    assert c.authorize(_rw_data(), "command", {"name": "DEL", "args": ["k"]})[0] == "deny"      # 破坏性
    assert c.authorize(_rw_esc(), "command", {"name": "DEL", "args": ["k"]})[0] == "confirm"
    assert c.authorize(_rw_esc(), "command", {"name": "FLUSHALL", "args": []})[0] == "deny"     # admin 恒拒


def test_document_uses_same_flow():
    c = MongoConnector(ConnectorConfig(dialect="mongodb"))
    assert c.authorize(_read_only(), "find", {"collection": "orders"})[0] == "allow"
    assert c.authorize(_read_only(), "insert_one", {"collection": "orders"})[0] == "deny"
    assert c.authorize(_rw_data(), "insert_one", {"collection": "orders"})[0] == "allow"
    assert c.authorize(_read_only(), "aggregate", {"pipeline": [{"$match": {}}]})[0] == "allow"
    assert c.authorize(_rw_data(), "aggregate", {"collection": "o", "pipeline": [{"$out": "x"}]})[0] == "deny"


def test_write_whitelist_applies_to_all_templates():
    acl_wl = _wl()
    m = MySQLConnector(ConnectorConfig(dialect="mysql"))
    assert m.authorize(acl_wl, "execute", {"sql": "INSERT INTO sales_t VALUES(1)"})[0] == "allow"
    assert m.authorize(acl_wl, "execute", {"sql": "INSERT INTO user_t VALUES(1)"})[0] == "deny"
    r = RedisConnector(ConnectorConfig(dialect="redis"))
    # key 前缀 sales* 可写
    assert r.authorize(Access.from_dict({"grant": "read+data", "write_allow": ["sales*"]}),
                       "command", {"name": "SET", "args": ["sales:1"]})[0] == "allow"
    assert r.authorize(Access.from_dict({"grant": "read+data", "write_allow": ["sales*"]}),
                       "command", {"name": "SET", "args": ["other:1"]})[0] == "deny"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个根层分级授权流程测试全部通过 ✅")
