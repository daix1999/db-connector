"""db-connector MCP server —— 多方言·多源集成·只读优先。

一个进程可同时挂载多个数据源（MySQL + Redis + Mongo…），由 env DB_SOURCES 配置。
每个工具带可选 `source` 参数定位到某个源；只有一个源时可省略。
族专属工具会校验目标源的方言族，不匹配则清晰报错。

只读护栏在"使用层"：连接器能读能写，但源级 allow_write=false 时写类工具/命令一律拒绝；
放开后仍对 Redis/Mongo 危险操作做额外约束。

本地起服务（stdio）：
    set DB_SOURCES=[{"name":"mysql","dialect":"mysql",...},...] & python -m mcp_server.server
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer  # noqa: E402
try:
    from mcp.server.mcpserver.tools.base import ToolError  # noqa: E402
except Exception:  # pragma: no cover
    class ToolError(Exception):  # type: ignore
        pass

from dbconnector import create, available_dialects  # noqa: E402
from dbconnector.base import RelationalConnector  # noqa: E402
from mcp_server import guard  # noqa: E402
from mcp_server.config import Source, ServerSettings, load_settings  # noqa: E402

_settings: ServerSettings | None = None
_conns: dict[str, object] = {}

mcp = MCPServer(
    "db-connector",
    instructions=(
        "本地数据库连接器（多方言·多源·只读优先）。先用 sources 看有哪些源，"
        "再带 source 参数调用：通用 health/list_sources/describe_source/get_source 跨方言；"
        "SQL 组 query/execute、Redis 组 redis_get/redis_scan/redis_command、"
        "Mongo 组 mongo_find/mongo_count/mongo_aggregate/mongo_write 按目标源方言校验。"
    ),
)


# ---------- 基础设施 ----------
def settings() -> ServerSettings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def _resolve_source(source: str | None) -> Source:
    srcs = settings().sources
    if source is None:
        d = settings().default_name()
        if d is None:
            raise ToolError(f"需指定 source，可选：{list(srcs)}")
        return srcs[d]
    if source not in srcs:
        raise ToolError(f"未知 source '{source}'，可选：{list(srcs)}")
    return srcs[source]


def _conn(src: Source):
    if src.name not in _conns:
        _conns[src.name] = create(src.config)
    return _conns[src.name]


def _family_of(src: Source) -> str:
    c = _conn(src)
    if isinstance(c, RelationalConnector):
        return "relational"
    return {"redis": "redis", "mongodb": "mongo"}.get(src.config.dialect, src.config.dialect)


def _target(source, family):
    """定位源并校验方言族。返回 (Source, connector)。"""
    src = _resolve_source(source)
    got = _family_of(src)
    if family and got != family:
        raise ToolError(f"该工具属 {family} 族，但源 '{src.name}' 方言={src.config.dialect}（族={got}）。")
    return src, _conn(src)


def _allow_write(src: Source):
    if not src.allow_write:
        raise ToolError(f"源 '{src.name}' 未开写：设置其 allow_write=true 后重启连接器。")


# ---------- 源发现 + 通用探查 ----------
@mcp.tool()
def sources() -> dict:
    """列出已配置的所有数据源：名称 / 方言 / 族 / 是否放开写。"""
    out = []
    for name, src in settings().sources.items():
        out.append({"name": name, "dialect": src.config.dialect, "family": _family_of(src),
                    "database": src.config.database, "allow_write": src.allow_write})
    return {"sources": out, "dialects_registered": available_dialects(),
            "default_source": settings().default_name()}


@mcp.tool()
def health(source: str | None = None) -> dict:
    """某源连接健康 + 族 + 是否放开写。不传 source 时（仅一个源）用默认源。"""
    src = _resolve_source(source)
    hc = _conn(src).health_check()
    hc["source"] = src.name
    hc["allow_write"] = src.allow_write
    hc["family"] = _family_of(src)
    return hc


@mcp.tool()
def list_sources(source: str | None = None) -> dict:
    """列某源的数据源：关系=表 / Mongo=集合 / Redis=key 概览。"""
    src = _resolve_source(source)
    return _conn(src).list_sources().dict()


@mcp.tool()
def describe_source(name: str, source: str | None = None) -> dict:
    """描述某源某数据结构（表字段 / 集合索引 / key 类型+TTL）。"""
    src = _resolve_source(source)
    return _conn(src).describe_source(name).dict()


@mcp.tool()
def get_source(name: str, limit: int = 20, source: str | None = None) -> dict:
    """取某源某数据若干样本。"""
    src = _resolve_source(source)
    limit = max(1, min(int(limit), src.max_rows))
    return _conn(src).get_source(name, limit).dict()


# ---------- SQL 族 ----------
@mcp.tool()
def query(sql: str, params: list | None = None, source: str | None = None) -> dict:
    """只读 SQL（仅 SELECT/SHOW/DESC/EXPLAIN，自动补 LIMIT）。目标源须是关系族。"""
    src, conn = _target(source, "relational")
    if not guard.is_read_only(sql):
        raise ToolError("只读模式：仅允许 SELECT/SHOW/DESC/DESCRIBE/EXPLAIN 语句。")
    try:
        final = guard.ensure_limit(sql, src.max_rows)
        res = conn.query(final, params or ())
        return {"source": src.name, "columns": res.columns, "rows": res.to_list(),
                "rowcount": res.rowcount, "executed_sql": final.strip()}
    except ValueError as e:
        raise ToolError(f"SQL 参数错误：{e}")


@mcp.tool()
def execute(sql: str, params: list | None = None, source: str | None = None) -> dict:
    """写 SQL（INSERT/UPDATE/DELETE/DDL）。需该源 allow_write=true。"""
    src, conn = _target(source, "relational")
    _allow_write(src)
    if guard.is_read_only(sql):
        raise ToolError("这是只读语句，请改用 query 工具。")
    return {"affected_rows": conn.execute(sql, params or ())}


# ---------- Redis 组 ----------
@mcp.tool()
def redis_get(key: str, source: str | None = None) -> dict:
    """读一个 key（按类型返回 string/hash/list/set/zset）。目标源须是 redis。"""
    src, conn = _target(source, "redis")
    return conn.get_source(key, limit=src.max_rows).dict()


@mcp.tool()
def redis_scan(match: str = "*", count: int = 100, source: str | None = None) -> dict:
    """游标扫描 key（只读、非阻塞）。"""
    src, conn = _target(source, "redis")
    keys = conn.scan(match=match, count=min(int(count), 1000))
    return {"source": src.name, "match": match, "keys": keys, "returned": len(keys)}


@mcp.tool()
def redis_command(name: str, args: list | None = None, source: str | None = None) -> dict:
    """任意 Redis 命令。只读源仅放行白名单命令；写命令需 allow_write；危险命令始终拒绝。"""
    src, conn = _target(source, "redis")
    if not guard.redis_read_only_ok(name):
        _allow_write(src)
        if name.upper() in guard.REDIS_DANGEROUS:
            raise ToolError(f"命令 {name} 属高危，本连接器拒绝执行（即使已开写）。")
    return {"source": src.name, "command": name, "result": conn.command(name, *(args or []))}


# ---------- Mongo 组 ----------
@mcp.tool()
def mongo_find(collection: str, filter: dict | None = None, limit: int = 50,
               projection: dict | None = None, sort: list | None = None,
               source: str | None = None) -> dict:
    """查询集合文档（只读）。目标源须是 mongodb。"""
    src, conn = _target(source, "mongo")
    docs = conn.find(collection, filter or {}, projection=projection, sort=sort,
                     limit=max(1, min(int(limit), src.max_rows)))
    return {"source": src.name, "collection": collection, "returned": len(docs), "documents": docs}


@mcp.tool()
def mongo_count(collection: str, filter: dict | None = None, source: str | None = None) -> dict:
    """统计集合文档数（只读）。"""
    src, conn = _target(source, "mongo")
    return {"source": src.name, "collection": collection, "count": conn.count(collection, filter or {})}


@mcp.tool()
def mongo_aggregate(collection: str, pipeline: list, source: str | None = None) -> dict:
    """聚合查询。含 $out/$merge 的写型管道需该源 allow_write=true。"""
    src, conn = _target(source, "mongo")
    if not guard.mongo_pipeline_read_only(pipeline):
        _allow_write(src)
    docs = conn.aggregate(collection, pipeline)
    return {"source": src.name, "collection": collection, "returned": len(docs), "documents": docs}


@mcp.tool()
def mongo_write(collection: str, operation: str, payload, source: str | None = None) -> dict:
    """写操作：insert / insert_many / update / delete。需该源 allow_write=true。"""
    src, conn = _target(source, "mongo")
    _allow_write(src)
    op = operation.lower()
    if op == "insert":
        return {"inserted_id": str(conn.insert_one(collection, payload))}
    if op == "insert_many":
        return {"inserted_ids": [str(x) for x in conn.insert_many(collection, payload)]}
    if op == "update":
        return {"modified_count": conn.update_one(collection, payload["filter"], payload["update"])}
    if op == "delete":
        return {"deleted_count": conn.delete(collection, payload if isinstance(payload, dict) else {})}
    raise ToolError(f"未知写操作: {operation}")


if __name__ == "__main__":
    mcp.run()   # 默认 stdio
