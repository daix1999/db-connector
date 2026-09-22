"""db-connector MCP server —— 面向 agent 的数据库连接器（多源·多数据模型·只读优先）。

设计目标是"agent 调用顺手"：
  - 自发现：先 sources 看有哪些源与已注册方言；再 list_sources/describe_source/get_source 摸清结构；
    最后按族用 query / redis_* / mongo_* 取数。跨方言探查方法统一，学一次到处用。
  - 自动扩展：新增数据库只要注册为方言插件（内置 connectors/ 或 pip entry_points），
    通用工具 health/list_sources/describe_source/get_source/sources 立即对它生效，无需改本文件。
  - 安全：连接器能力全开，但本层默认只读（源级 allow_write=false），并对危险操作硬拦。

配置：env DB_SOURCES（JSON 数组，见 config.py）。本地起服务：
    set DB_SOURCES=[{"name":"mysql","dialect":"mysql",...}] & python -m mcp_server.server
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

from dbconnector import create, dialect_info  # noqa: E402
from mcp_server import guard  # noqa: E402
from mcp_server.config import Source, ServerSettings, load_settings  # noqa: E402

_settings: ServerSettings | None = None
_conns: dict[str, object] = {}

mcp = MCPServer(
    "db-connector",
    instructions=(
        "数据库连接器（多源·多数据模型·只读优先）。推荐调用顺序："
        "sources（看有哪些源/方言）→ list_sources(source) → describe_source(name,source) → "
        "query/get_source。通用工具跨方言可用；query/execute 面向 relational 族，"
        "redis_get/redis_scan/redis_command 面向 keyvalue 族，mongo_find/mongo_count/"
        "mongo_aggregate/mongo_write 面向 document 族。写操作需该源 allow_write=true。"
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
    if not srcs:
        raise ToolError("未配置任何数据源：请设置 env DB_SOURCES（见 README）。")
    if source is None:
        d = settings().default_name()
        if d is None:
            raise ToolError(f"存在多个源，需指定 source 参数；可选：{list(srcs)}")
        return srcs[d]
    if source not in srcs:
        raise ToolError(f"未知 source '{source}'；可选：{list(srcs)}（先调 sources 查看）")
    return srcs[source]


def _conn(src: Source):
    if src.name not in _conns:
        _conns[src.name] = create(src.config)
    return _conns[src.name]


def _family_of(src: Source) -> str:
    """归一到数据模型族名；列式并入 relational。"""
    fam = getattr(_conn(src), "data_model", None) or src.config.dialect
    return "relational" if fam == "columnar" else fam


def _target(source, family):
    src = _resolve_source(source)
    got = _family_of(src)
    if family and got != family:
        raise ToolError(
            f"该工具面向 {family} 族，但源 '{src.name}' 属于 {got} 族。"
            f"请改用匹配 {got} 族的工具（health/list_sources/describe_source/get_source 对所有族通用）。")
    return src, _conn(src)


def _allow_write(src: Source):
    if not src.allow_write:
        raise ToolError(f"源 '{src.name}' 为只读。确需写入：在其 DB_SOURCES 配置里设 "
                        f"\"allow_write\": true 并重启该连接器。")


# ---------- 源发现 + 通用探查（跨方言）----------
@mcp.tool()
def sources() -> dict:
    """列出已配置的源与全部可注册方言。agent 应首先调用本工具再决定 source。"""
    configured = [{"name": n, "dialect": s.config.dialect, "family": _family_of(s),
                   "database": s.config.database, "allow_write": s.allow_write}
                  for n, s in settings().sources.items()]
    return {
        "configured_sources": configured,
        "registered_dialects": dialect_info(),  # 每个方言 -> family / default_port
        "default_source": settings().default_name(),
        "hint": "用 list_sources(source=...) 看某个源里有哪些表/集合/key",
    }


@mcp.tool()
def health(source: str | None = None) -> dict:
    """某源连通性与元信息（方言/族/是否可写）。"""
    src = _resolve_source(source)
    hc = _conn(src).health_check()
    hc.update({"source": src.name, "allow_write": src.allow_write, "family": _family_of(src)})
    hc["next"] = f"list_sources(source='{src.name}') 看该源有哪些数据"
    return hc


@mcp.tool()
def list_sources(source: str | None = None) -> dict:
    """列某源的数据单元：关系=表 / 文档=集合 / 键值=key 概览 / 向量=集合 …（统一接口）。"""
    src = _resolve_source(source)
    d = _conn(src).list_sources().dict()
    d["source"] = src.name
    d["next"] = f"describe_source(name, source='{src.name}') 看结构，或 get_source/query 取数"
    return d


@mcp.tool()
def describe_source(name: str, source: str | None = None) -> dict:
    """描述某源某数据单元结构（字段/索引/key+TTL/集合维…）。"""
    src = _resolve_source(source)
    try:
        d = _conn(src).describe_source(name).dict()
    except NotImplementedError as e:
        raise ToolError(str(e))
    d["source"] = src.name
    return d


@mcp.tool()
def get_source(name: str, limit: int = 20, source: str | None = None) -> dict:
    """取某源某数据单元样本（无需写 SQL，适合快速看数据长相）。"""
    src = _resolve_source(source)
    limit = max(1, min(int(limit), src.max_rows))
    try:
        d = _conn(src).get_source(name, limit).dict()
    except NotImplementedError as e:
        raise ToolError(str(e))
    d["source"] = src.name
    return d


# ---------- relational 族（SQL）----------
@mcp.tool()
def query(sql: str, params: list | None = None, source: str | None = None) -> dict:
    """只读 SQL（SELECT/SHOW/DESC/EXPLAIN），自动补 LIMIT。目标源须是 relational 族。"""
    src, conn = _target(source, "relational")
    if not guard.is_read_only(sql):
        raise ToolError("query 仅允许只读语句。需要写数据请用 execute（且该源需 allow_write=true）。")
    final = guard.ensure_limit(sql, src.max_rows)
    try:
        res = conn.query(final, params or ())
        return {"source": src.name, "columns": res.columns, "rows": res.to_list(),
                "rowcount": res.rowcount, "executed_sql": final.strip()}
    except Exception as e:
        raise ToolError(f"查询失败：{e}")


@mcp.tool()
def execute(sql: str, params: list | None = None, source: str | None = None) -> dict:
    """写 SQL（INSERT/UPDATE/DELETE/DDL）。需该源 allow_write=true；禁止多语句拼接。"""
    src, conn = _target(source, "relational")
    _allow_write(src)
    if guard.has_multiple_statements(sql):
        raise ToolError("出于安全，execute 一次只允许一条语句，拒绝多语句拼接。")
    if guard.is_read_only(sql):
        raise ToolError("这是只读语句，请改用 query 工具。")
    try:
        return {"source": src.name, "affected_rows": conn.execute(sql, params or ())}
    except Exception as e:
        raise ToolError(f"执行失败：{e}")


# ---------- keyvalue 族（Redis…）----------
@mcp.tool()
def redis_get(key: str, source: str | None = None) -> dict:
    """读一个 key（按类型返回 string/hash/list/set/zset）。目标源须是 keyvalue 族。"""
    src, conn = _target(source, "keyvalue")
    return {"source": src.name, **conn.get_source(key, limit=src.max_rows).dict()}


@mcp.tool()
def redis_scan(match: str = "*", count: int = 100, source: str | None = None) -> dict:
    """游标扫描 key（只读、非阻塞）。"""
    src, conn = _target(source, "keyvalue")
    keys = conn.scan(match=match, count=min(int(count), 1000))
    return {"source": src.name, "match": match, "keys": keys, "returned": len(keys)}


@mcp.tool()
def redis_command(name: str, args: list | None = None, source: str | None = None) -> dict:
    """任意 Redis 命令。只读源仅放行白名单命令；写命令需 allow_write；危险命令始终拒绝。"""
    src, conn = _target(source, "keyvalue")
    if not guard.redis_read_only_ok(name):
        _allow_write(src)
        if name.upper() in guard.REDIS_DANGEROUS:
            raise ToolError(f"命令 {name} 属高危（清库/改配置/关服务），本连接器始终拒绝。")
    try:
        return {"source": src.name, "command": name, "result": conn.command(name, *(args or []))}
    except Exception as e:
        raise ToolError(f"命令执行失败：{e}")


# ---------- document 族（Mongo…）----------
@mcp.tool()
def mongo_find(collection: str, filter: dict | None = None, limit: int = 50,
               projection: dict | None = None, sort: list | None = None,
               source: str | None = None) -> dict:
    """查询集合文档（只读）。目标源须是 document 族。"""
    src, conn = _target(source, "document")
    docs = conn.find(collection, filter or {}, projection=projection, sort=sort,
                     limit=max(1, min(int(limit), src.max_rows)))
    return {"source": src.name, "collection": collection, "returned": len(docs), "documents": docs}


@mcp.tool()
def mongo_count(collection: str, filter: dict | None = None, source: str | None = None) -> dict:
    """统计集合文档数（只读）。"""
    src, conn = _target(source, "document")
    return {"source": src.name, "collection": collection, "count": conn.count(collection, filter or {})}


@mcp.tool()
def mongo_aggregate(collection: str, pipeline: list, source: str | None = None) -> dict:
    """聚合查询。含 $out/$merge 的写型管道需该源 allow_write=true。"""
    src, conn = _target(source, "document")
    if not guard.mongo_pipeline_read_only(pipeline):
        _allow_write(src)
    docs = conn.aggregate(collection, pipeline)
    return {"source": src.name, "collection": collection, "returned": len(docs), "documents": docs}


@mcp.tool()
def mongo_write(collection: str, operation: str, payload, source: str | None = None) -> dict:
    """写操作：insert / insert_many / update / delete。需该源 allow_write=true。"""
    src, conn = _target(source, "document")
    _allow_write(src)
    op = operation.lower()
    try:
        if op == "insert":
            return {"inserted_id": str(conn.insert_one(collection, payload))}
        if op == "insert_many":
            return {"inserted_ids": [str(x) for x in conn.insert_many(collection, payload)]}
        if op == "update":
            return {"modified_count": conn.update_one(collection, payload["filter"], payload["update"])}
        if op == "delete":
            return {"deleted_count": conn.delete(collection, payload if isinstance(payload, dict) else {})}
    except Exception as e:
        raise ToolError(f"写操作失败：{e}")
    raise ToolError(f"未知写操作: {operation}（支持 insert/insert_many/update/delete）")


if __name__ == "__main__":
    mcp.run()   # 默认 stdio
