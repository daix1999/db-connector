"""MCP server 端到端自测：以 stdio 拉起 server 子进程，用官方 client 握手并调用工具。

校验：握手成功、6 个工具齐全、只读工具返回真实数据、越权写语句被干净拒绝。

用法：
    python tests/test_mcp_stdio.py --user root --password "你的密码" --database test
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mcp import ClientSession                       # noqa: E402
from mcp.client.stdio import stdio_client, StdioServerParameters  # noqa: E402
from dbconnector import connect                     # noqa: E402


def payload(res):
    sc = getattr(res, "structured_content", None)
    if sc is None:
        sc = json.loads(res.content[0].text)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        sc = sc["result"]
    return sc


async def run(a):
    os.environ.update({
        "DB_HOST": a.host, "DB_PORT": str(a.port), "DB_USER": a.user,
        "DB_PASSWORD": a.password, "DB_DATABASE": a.database, "DB_ALLOW_WRITE": "false",
    })
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"],
                                   cwd=ROOT, env=dict(os.environ))
    results = []

    def check(name, cond, extra=""):
        results.append(cond)
        print(f"  {'[PASS]' if cond else '[FAIL]'} {name} {extra}")

    # 准备一张已知表
    db = connect("mysql", host=a.host, port=a.port, user=a.user,
                 password=a.password, database=a.database)
    db.execute("DROP TABLE IF EXISTS _mcp_probe")
    db.execute("CREATE TABLE _mcp_probe(id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(50), v INT)")
    db.execute_many("INSERT INTO _mcp_probe(name,v) VALUES(%s,%s)", [("x", 10), ("y", 20)])
    db.close()

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            check("握手成功", init.server_info.name == "db-connector",
                  f"(proto {init.protocol_version})")
            tools = {t.name for t in (await s.list_tools()).tools}
            expected = {"health", "sources", "list_sources", "describe_source", "get_source",
                        "query", "execute",
                        "redis_get", "redis_scan", "redis_command",
                        "mongo_find", "mongo_count", "mongo_aggregate", "mongo_write"}
            check("工具集完整(源发现+通用+SQL+Redis+Mongo)", expected <= tools, f"缺失 {expected - tools}")

            h = payload(await s.call_tool("health", {}))
            check("health ok", h.get("ok") is True and h.get("allow_write") is False
                  and h.get("family") == "relational")

            ls = payload(await s.call_tool("list_sources", {}))
            check("list_sources 含探测表", any(x["source_name"] == "_mcp_probe" for x in ls["rows"]))

            dsc = [c["col_name"] for c in payload(await s.call_tool("describe_source", {"name": "_mcp_probe"}))["rows"]]
            check("describe_source 列正确", dsc == ["id", "name", "v"], f"-> {dsc}")

            q = payload(await s.call_tool("query", {"sql": "SELECT name,v FROM _mcp_probe ORDER BY v DESC"}))
            check("query 只读返回正确", [row["name"] for row in q["rows"]] == ["y", "x"]
                  and "LIMIT" in q["executed_sql"].upper())

            bad = await s.call_tool("query", {"sql": "DELETE FROM _mcp_probe"})
            check("越权 DELETE 被拒", getattr(bad, "is_error", False))

            we = await s.call_tool("execute", {"sql": "UPDATE _mcp_probe SET v=0 WHERE id=1"})
            check("只读模式 execute 被拒", getattr(we, "is_error", False))

            rf = await s.call_tool("mongo_find", {"collection": "x"})
            check("mongo 工具在 mysql 方言下被族守卫拦截", getattr(rf, "is_error", False))

    # 清理
    db = connect("mysql", host=a.host, port=a.port, user=a.user,
                 password=a.password, database=a.database)
    db.execute("DROP TABLE IF EXISTS _mcp_probe")
    db.close()

    print(f"\n{'全部通过 ✅' if all(results) else '存在失败 ❌'}  ({sum(results)}/{len(results)})")
    return 0 if all(results) else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.getenv("DB_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    p.add_argument("--user", default=os.getenv("DB_USER", "root"))
    p.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    p.add_argument("--database", default=os.getenv("DB_DATABASE", "test"))
    a = p.parse_args()
    sys.exit(asyncio.run(run(a)))


if __name__ == "__main__":
    main()
