"""读写分离分级授权 + 确认流 的真实 stdio 端到端测试（需 MySQL + Redis 容器）。

用法： python tests/test_permissions.py --user root --password root123
"""
from __future__ import annotations
import argparse, asyncio, json, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcp import ClientSession                                       # noqa: E402
from mcp.client.stdio import stdio_client, StdioServerParameters    # noqa: E402
from dbconnector import connect                                     # noqa: E402


def payload(res):
    sc = getattr(res, "structured_content", None)
    if sc is None:
        sc = json.loads(res.content[0].text)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        sc = sc["result"]
    return sc


R = []
def ck(name, cond, extra=""):
    R.append(cond)
    print(f"  {'[PASS]' if cond else '[FAIL]'} {name} {extra}")


async def run(a):
    host = a.host
    SOURCES = [
        # 只到数据写，无升级：DROP 应被拒
        {"name": "rw", "dialect": "mysql", "host": host, "port": a.port, "user": a.user,
         "password": a.password, "database": "test",
         "access": {"grant": "read+data"}},
        # 数据写免确认，破坏性需确认（可升级）
        {"name": "confirm", "dialect": "mysql", "host": host, "port": a.port, "user": a.user,
         "password": a.password, "database": "test",
         "access": {"grant": "read+data", "allow_escalation": True}},
        # 写白名单：只有 pw_* 表可写
        {"name": "wl", "dialect": "mysql", "host": host, "port": a.port, "user": a.user,
         "password": a.password, "database": "test",
         "access": {"grant": "read+data", "write_allow": ["pw_*"]}},
        # redis：破坏性 DEL 需确认，FLUSHALL admin 恒拒
        {"name": "cache", "dialect": "redis", "host": host, "port": 6379, "database": 5,
         "access": {"grant": "read", "allow_escalation": True}},
    ]
    # 预置表与 key
    for t in ("rw_t", "pw_ok", "other_t"):
        d = connect("mysql", host=host, port=a.port, user=a.user, password=a.password, database="test")
        d.execute(f"DROP TABLE IF EXISTS {t}")
        d.execute(f"CREATE TABLE {t}(id INT PRIMARY KEY AUTO_INCREMENT, v VARCHAR(20))")
        d.execute(f"INSERT INTO {t}(v) VALUES('seed')")
        d.close()
    rc = connect("redis", host=host, port=6379, database=5); rc.execute_command("SET", "k1", "v1"); rc.close()

    env = dict(os.environ); env["DB_SOURCES"] = json.dumps(SOURCES)
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], cwd=ROOT, env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            # 读：全部放行
            q = payload(await s.call_tool("query", {"sql": "SELECT v FROM rw_t", "source": "rw"}))
            ck("只读放行", q["rows"][0]["v"] == "seed")

            # rw: 数据写放行；DROP 被拒（无升级）
            ins = payload(await s.call_tool("execute", {"sql": "INSERT INTO rw_t(v) VALUES(%s)",
                                                        "params": ["x"], "source": "rw"}))
            ck("grant=read+data 时 INSERT 放行", ins.get("affected_rows") == 1)
            drp = await s.call_tool("execute", {"sql": "DROP TABLE rw_t", "source": "rw"})
            ck("grant=read+data 时 DROP 被拒(无升级)", drp.is_error and "超出" in drp.content[0].text)

            # confirm: DROP 先要确认，带令牌二次执行
            c1 = await s.call_tool("execute", {"sql": "DROP TABLE rw_t", "source": "confirm"})
            gate = payload(c1)
            ck("破坏性操作返回 requires_confirmation", (not c1.is_error) and gate.get("requires_confirmation") is True
               and "DESTRUCTIVE" in gate.get("risk_level", ""))
            tok = gate["confirm_token"]
            c2 = await s.call_tool("execute", {"sql": "DROP TABLE rw_t", "source": "confirm", "confirm": tok})
            ck("带确认令牌执行成功", not c2.is_error and payload(c2).get("affected_rows") in (0, 1))
            # 令牌不可跨操作复用
            c3 = await s.call_tool("execute", {"sql": "DROP TABLE other_t", "source": "confirm", "confirm": tok})
            ck("令牌对不同操作无效(再次要求确认)", not c3.is_error and payload(c3).get("requires_confirmation") is True)

            # write_allow: pw_ok 可写，other_t 不可
            okw = await s.call_tool("execute", {"sql": "INSERT INTO pw_ok(v) VALUES(%s)", "params": ["y"], "source": "wl"})
            ckw = await s.call_tool("execute", {"sql": "INSERT INTO other_t(v) VALUES(%s)", "params": ["z"], "source": "wl"})
            ck("白名单内可写", not okw.is_error)
            ck("白名单外被拒", ckw.is_error and "白名单" in ckw.content[0].text)

            # redis: 读放行；DEL 破坏性需确认；FLUSHALL admin 恒拒
            rg = payload(await s.call_tool("redis_get", {"key": "k1", "source": "cache"}))
            ck("redis 读放行", rg.get("scalar") == "v1")
            dl = await s.call_tool("redis_command", {"name": "DEL", "args": ["k1"], "source": "cache"})
            ck("redis DEL 需确认", payload(dl).get("requires_confirmation") is True)
            fa = await s.call_tool("redis_command", {"name": "FLUSHALL", "args": [], "source": "cache"})
            ck("FLUSHALL 恒拒(管理员级)", fa.is_error and "永久拒绝" in fa.content[0].text)

    # 清表
    d = connect("mysql", host=host, port=a.port, user=a.user, password=a.password, database="test")
    for t in ("rw_t", "pw_ok", "other_t"):
        d.execute(f"DROP TABLE IF EXISTS {t}")
    d.close()
    print(f"\n权限分级+确认流 {'全部通过 ✅' if all(R) else '存在失败 ❌'} ({sum(R)}/{len(R)})")
    return 0 if all(R) else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--user", default="root")
    p.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
