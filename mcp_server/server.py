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

from dbconnector import create, dialect_info, levels  # noqa: E402
from mcp_server import guard  # noqa: E402
from mcp_server.audit import audited  # noqa: E402
from mcp_server.config import Source, ServerSettings, load_settings  # noqa: E402

_settings: ServerSettings | None = None
_conns: dict[str, object] = {}

mcp = MCPServer(
    "db-connector",
    instructions=(
        "数据库连接器（多源·读写分离分级授权）。调用顺序：sources → list_sources(source) → "
        "describe_source(name,source) → query/get_source。通用工具跨方言；query/execute 属 relational，"
        "redis_* 属 keyvalue，mongo_* 属 document。读默认放行；写按源 grant 免确认等级放行，"
        "超出的操作返回 requires_confirmation+confirm_token，带 confirm 参数二次调用才执行（管理员级恒拒）。"
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


def _authorize(src: Source, conn, op: str, args: dict, confirm: str | None):
    """统一授权闸门：调用根层 conn.authorize()（所有模板同一流程），MCP 只加确认令牌。
    返回 None=放行；dict=需确认（未执行）；抛 ToolError=拒绝。"""
    verdict, reason, level = conn.authorize(src.access, op, args)
    if verdict == "allow":
        return None
    _, target = conn.classify(op, args)
    if verdict == "confirm":
        token, exp = guard.issue_token(src.name, op, level, target)
        if confirm and guard.verify_token(confirm, src.name, op, level, target):
            return None   # 已确认 → 放行
        return {
            "requires_confirmation": True, "source": src.name, "op": op,
            "risk_level": levels.level_name(level), "target": target,
            "intent": reason, "confirm_token": token, "expires_at_epoch": exp,
            "next": f"确认该操作后，用相同参数再调一次并附 confirm=\"{token}\"",
        }
    raise ToolError(reason)   # deny


def _access_view(src: Source) -> dict:
    a = src.access
    return {"read": a.read, "grant": levels.level_name(a.grant_max),
            "confirm_from": levels.level_name(a.confirm_from),
            "write_allow": a.write_allow, "write_deny": a.write_deny}


# ---------- 源发现 + 通用探查（跨方言）----------
@mcp.tool()
@audited("sources")
def sources() -> dict:
    """列出已配置的源与全部可注册方言。agent 应首先调用本工具再决定 source。"""
    configured = [{"name": n, "dialect": s.config.dialect, "family": _family_of(s),
                   "database": s.config.database, "access": _access_view(s)}
                  for n, s in settings().sources.items()]
    return {
        "configured_sources": configured,
        "registered_dialects": dialect_info(),  # 每个方言 -> family / default_port
        "default_source": settings().default_name(),
        "hint": "用 list_sources(source=...) 看某个源里有哪些表/集合/key",
    }


@mcp.tool()
@audited("health")
def health(source: str | None = None) -> dict:
    """某源连通性与元信息（方言/族/权限视图）。"""
    src = _resolve_source(source)
    hc = _conn(src).health_check()
    hc.update({"source": src.name, "family": _family_of(src), "access": _access_view(src)})
    hc["next"] = f"list_sources(source='{src.name}') 看该源有哪些数据"
    return hc


@mcp.tool()
@audited("list_sources")
def list_sources(source: str | None = None) -> dict:
    """列某源的数据单元：关系=表 / 文档=集合 / 键值=key 概览 / 向量=集合 …（统一接口）。"""
    src = _resolve_source(source)
    d = _conn(src).list_sources().dict()
    d["source"] = src.name
    d["next"] = f"describe_source(name, source='{src.name}') 看结构，或 get_source/query 取数"
    return d


@mcp.tool()
@audited("describe_source")
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
@audited("get_source")
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
@audited("query")
def query(sql: str, params: list | None = None, source: str | None = None) -> dict:
    """只读 SQL（SELECT/SHOW/DESC/EXPLAIN），自动补 LIMIT。目标源须是 relational 族。"""
    src, conn = _target(source, "relational")
    if not guard.is_read_only(sql):
        raise ToolError("query 仅允许只读语句。需要写数据请用 execute（按该源 grant/确认放行）。")
    gate = _authorize(src, conn, "query", {}, None)
    if gate:
        return gate
    final = guard.ensure_limit(sql, src.max_rows)
    try:
        res = conn.query(final, params or ())
        return {"source": src.name, "columns": res.columns, "rows": res.to_list(),
                "rowcount": res.rowcount, "executed_sql": final.strip()}
    except Exception as e:
        raise ToolError(f"查询失败：{e}")


@mcp.tool()
@audited("execute")
def execute(sql: str, params: list | None = None, source: str | None = None,
            confirm: str | None = None) -> dict:
    """写 SQL（INSERT/UPDATE/DELETE/DDL）。按源 grant 分级放行；超阈值返回确认令牌，带 confirm 二次执行；禁止多语句。"""
    src, conn = _target(source, "relational")
    if guard.has_multiple_statements(sql):
        raise ToolError("出于安全，execute 一次只允许一条语句，拒绝多语句拼接。")
    if guard.is_read_only(sql):
        raise ToolError("这是只读语句，请改用 query 工具。")
    gate = _authorize(src, conn, "execute", {"sql": sql}, confirm)
    if gate:
        return gate
    try:
        return {"source": src.name, "affected_rows": conn.execute(sql, params or ())}
    except Exception as e:
        raise ToolError(f"执行失败：{e}")


# ---------- keyvalue 族（Redis…）----------
@mcp.tool()
@audited("redis_get")
def redis_get(key: str, source: str | None = None) -> dict:
    """读一个 key（按类型返回 string/hash/list/set/zset）。目标源须是 keyvalue 族。"""
    src, conn = _target(source, "keyvalue")
    return {"source": src.name, **conn.get_source(key, limit=src.max_rows).dict()}


@mcp.tool()
@audited("redis_scan")
def redis_scan(match: str = "*", count: int = 100, source: str | None = None) -> dict:
    """游标扫描 key（只读、非阻塞）。"""
    src, conn = _target(source, "keyvalue")
    keys = conn.scan(match=match, count=min(int(count), 1000))
    return {"source": src.name, "match": match, "keys": keys, "returned": len(keys)}


@mcp.tool()
@audited("redis_command")
def redis_command(name: str, args: list | None = None, source: str | None = None,
                  confirm: str | None = None) -> dict:
    """任意 Redis 命令。按 levels 分级 + 源 grant 放行；超阈值返回确认令牌；管理员级(FLUSHALL/CONFIG/SHUTDOWN…)恒拒。"""
    src, conn = _target(source, "keyvalue")
    gate = _authorize(src, conn, "command", {"name": name, "args": args or []}, confirm)
    if gate:
        return gate
    try:
        return {"source": src.name, "command": name, "result": conn.command(name, *(args or []))}
    except Exception as e:
        raise ToolError(f"命令执行失败：{e}")


# ---------- document 族（Mongo…）----------
@mcp.tool()
@audited("mongo_find")
def mongo_find(collection: str, filter: dict | None = None, limit: int = 50,
               projection: dict | None = None, sort: list | None = None,
               source: str | None = None) -> dict:
    """查询集合文档（只读）。目标源须是 document 族。"""
    src, conn = _target(source, "document")
    docs = conn.find(collection, filter or {}, projection=projection, sort=sort,
                     limit=max(1, min(int(limit), src.max_rows)))
    return {"source": src.name, "collection": collection, "returned": len(docs), "documents": docs}


@mcp.tool()
@audited("mongo_count")
def mongo_count(collection: str, filter: dict | None = None, source: str | None = None) -> dict:
    """统计集合文档数（只读）。"""
    src, conn = _target(source, "document")
    return {"source": src.name, "collection": collection, "count": conn.count(collection, filter or {})}


@mcp.tool()
@audited("mongo_aggregate")
def mongo_aggregate(collection: str, pipeline: list, source: str | None = None,
                    confirm: str | None = None) -> dict:
    """聚合查询。含 $out/$merge 的写型管道按破坏性级处理（按 grant/确认放行）。"""
    src, conn = _target(source, "document")
    gate = _authorize(src, conn, "aggregate", {"collection": collection, "pipeline": pipeline}, confirm)
    if gate:
        return gate
    docs = conn.aggregate(collection, pipeline)
    return {"source": src.name, "collection": collection, "returned": len(docs), "documents": docs}


_MONGO_WRITE_OP = {"insert": "insert_one", "insert_many": "insert_many",
                   "update": "update_one", "delete": "delete"}


@mcp.tool()
@audited("mongo_write")
def mongo_write(collection: str, operation: str, payload, source: str | None = None,
                confirm: str | None = None) -> dict:
    """写操作：insert / insert_many / update / delete。按 grant 分级 + 确认放行。"""
    src, conn = _target(source, "document")
    op = operation.lower()
    canon = _MONGO_WRITE_OP.get(op, op)
    gate = _authorize(src, conn, canon, {"collection": collection}, confirm)
    if gate:
        return gate
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
