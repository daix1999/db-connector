"""权限档来源测试：DB_ACCESS_PROFILE_FILE + DB_ACCESS_PROFILES 合并 + 内联覆盖 profile。"""
from __future__ import annotations
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dbconnector import levels                       # noqa: E402
import mcp_server.config as C                         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _clear_env():
    for k in ("DB_ACCESS_PROFILE_FILE", "DB_ACCESS_PROFILES", "DB_SOURCES",
              "DB_DIALECT", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_DATABASE", "DB_DSN"):
        os.environ.pop(k, None)


def test_load_from_file():
    _clear_env()
    os.environ["DB_ACCESS_PROFILE_FILE"] = os.path.join(ROOT, "access_profiles.json")
    os.environ["DB_SOURCES"] = json.dumps([
        {"name": "m", "dialect": "mysql", "database": "biz", "access": "readonly"},
        {"name": "w", "dialect": "mysql", "database": "x", "access": "cache"},
    ])
    s = C.load_settings()
    assert s.sources["m"].access.grant_max == levels.READ
    assert s.sources["w"].access.grant_max == levels.WRITE_DATA


def test_inline_overrides_file():
    _clear_env()
    os.environ["DB_ACCESS_PROFILE_FILE"] = os.path.join(ROOT, "access_profiles.json")
    os.environ["DB_ACCESS_PROFILES"] = json.dumps({"readonly": {"grant": "read+data"}})  # 临时覆盖档
    os.environ["DB_SOURCES"] = json.dumps([{"name": "m", "dialect": "mysql", "access": "readonly"}])
    s = C.load_settings()
    assert s.sources["m"].access.grant_max == levels.WRITE_DATA   # 内联 profile 覆盖文件


def test_profile_plus_inline_override():
    _clear_env()
    os.environ["DB_ACCESS_PROFILE_FILE"] = os.path.join(ROOT, "access_profiles.json")
    os.environ["DB_SOURCES"] = json.dumps([
        {"name": "ops", "dialect": "mysql", "access": {"profile": "prod", "write_deny": ["secret_t"]}},
    ])
    s = C.load_settings()
    a = s.sources["ops"].access
    assert a.grant_max == levels.READ                      # 来自 prod 档
    assert a.allow_escalation is True                      # 来自 prod 档
    assert a.write_deny == ["secret_t"]                    # 内联补充


def test_relative_profile_path_resolves():
    _clear_env()
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        os.environ["DB_ACCESS_PROFILE_FILE"] = "access_profiles.json"
        os.environ["DB_SOURCES"] = json.dumps([{"name": "m", "dialect": "mysql", "access": "sandbox"}])
        s = C.load_settings()
        assert s.sources["m"].access.grant_max == levels.DESTRUCTIVE
    finally:
        os.chdir(cwd)
        _clear_env()


def test_missing_profile_file_tolerated():
    _clear_env()
    os.environ["DB_ACCESS_PROFILE_FILE"] = os.path.join(tempfile.mkdtemp(), "nope.json")
    os.environ["DB_SOURCES"] = json.dumps([{"name": "m", "dialect": "mysql", "access": "nosuch"}])
    s = C.load_settings()
    assert s.sources["m"].access.grant_max == levels.READ   # 未知档 → 最安全只读


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for f in fns:
        f()
        print(f"  PASS  {f.__name__}")
    print(f"\n{len(fns)} 个权限档来源测试全部通过 ✅")
