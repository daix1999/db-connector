"""离线单元测试：不依赖真实数据库，验证注册表 / 配置 / 结果封装的正确性。

运行： python -m pytest -q   或   python tests/test_offline.py
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dbconnector import (  # noqa: E402
    available_dialects, get_class, connect, ConnectorConfig,
    PoolConfig, ResultSet, DialectNotRegisteredError,
)


def test_mysql_registered():
    assert "mysql" in available_dialects()
    assert get_class("mysql").dialect == "mysql"


def test_unknown_dialect_raises():
    try:
        get_class("nosuchdb")
        assert False, "应抛 DialectNotRegisteredError"
    except DialectNotRegisteredError:
        pass


def test_from_kwargs_splits_pool_and_extra():
    cfg = ConnectorConfig.from_kwargs(
        "mysql", host="h", port=3307, user="u", password="p",
        database="d", maxconnections=42, mincached=2, charset="latin1",
    )
    assert cfg.host == "h" and cfg.port == 3307 and cfg.database == "d"
    assert cfg.pool.maxconnections == 42 and cfg.pool.mincached == 2
    assert cfg.extra == {"charset": "latin1"}  # 未知参数落入 extra


def test_mysql_connect_kwargs_defaults():
    db = connect("mysql", host="127.0.0.1", user="root", password="x", database="test")
    kw = db._connect_kwargs()
    assert kw["port"] == 3306          # 默认端口
    assert kw["charset"] == "utf8mb4"  # 默认字符集
    assert kw["database"] == "test"


def test_result_set_helpers():
    rs = ResultSet(columns=["id", "name"],
                   rows=[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
    assert len(rs) == 2 and rs.scalar == 1
    assert rs.first()["name"] == "a"
    assert rs.column("name") == ["a", "b"]


def test_pool_config_defaults():
    p = PoolConfig()
    assert p.mincached == 1 and p.maxconnections == 10 and p.ping == 1


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
            passed += 1
    print(f"\n{passed} 个离线测试全部通过 ✅")
