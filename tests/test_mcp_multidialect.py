"""多方言 stdio 护栏测试：验证 Redis / Mongo 方言的"族守卫 + 只读拒绝"路径
   ——这些路径在连接网络前就被拦截，因此无需真实 Redis/Mongo 服务即可运行。

用法： python tests/test_mcp_multidialect.py
"""
from __future__ import annotations
import asyncio, json, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mcp import ClientSession                                   # noqa: E402
from mcp.client.stdio import stdio_client, StdioServerParameters  # noqa: E402


def payload(res):
    sc = getattr(res, "structured_content", None)
    if sc is None:
        sc = json.loads(res.content[0].text)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        sc = sc["result"]
    return sc


def session(dialect: str, extra_env: dict):
    os.environ.update({"DB_DIALECT": dialect, "DB_HOST": "127.0.0.1", **extra_env})
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"],
                                   cwd=ROOT, env=dict(os.environ))
    return stdio_client(params)


def check(buf, name, cond, extra=""):
    buf.append(cond)
    print(f"  {'[PASS]' if cond else '[FAIL]'} [{name}] {extra}")


async def main():
    results = []

    # ---- Redis 方言 ----
    async with session("redis", {"DB_ALLOW_WRITE": "false"}) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            h = payload(await s.call_tool("health", {}))
            check(results, "redis", h.get("dialect") == "redis" and h.get("family") == "keyvalue")
            # 写命令在只读模式被拦（网络前）
            d = await s.call_tool("redis_command", {"name": "SET", "args": ["k", "v"]})
            check(results, "redis", d.is_error and "只读" in d.content[0].text)
            # SQL 工具在 redis 方言被族守卫拦
            q = await s.call_tool("query", {"sql": "SELECT 1"})
            check(results, "redis", q.is_error and "relational" in q.content[0].text)
            # 只读命令可过守卫（之后可能因无服务失败，这里只断言"没被只读守卫拦"）
            g = await s.call_tool("redis_command", {"name": "GET", "args": ["k"]})
            check(results, "redis", not (g.is_error and "只读" in g.content[0].text),
                  "GET 未被只读守卫误拦")

    # ---- Mongo 方言 ----
    async with session("mongodb", {"DB_ALLOW_WRITE": "false", "DB_DATABASE": "testdb"}) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            h = payload(await s.call_tool("health", {}))
            check(results, "mongo", h.get("dialect") == "mongodb" and h.get("family") == "document")
            d = await s.call_tool("mongo_write", {"collection": "c", "operation": "insert",
                                                   "payload": {"a": 1}})
            check(results, "mongo", d.is_error and "只读" in d.content[0].text)
            p = await s.call_tool("mongo_aggregate", {"collection": "c",
                                                      "pipeline": [{"$out": "x"}]})
            check(results, "mongo", p.is_error and "只读" in p.content[0].text)
            q = await s.call_tool("redis_get", {"key": "k"})
            check(results, "mongo", q.is_error and "keyvalue" in q.content[0].text)

    print(f"\n{'全部通过 ✅' if all(results) else '存在失败 ❌'}  ({sum(results)}/{len(results)})")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
