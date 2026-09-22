"""操作决策分析（纯静态、与执行同源、不发确认令牌、不连库）。

给一个"具体操作 + 目标源"，产出一张决策卡：意图归类 / 风险级 / 授权判定 /
执行预览 / 建议下一步。判定与真正执行用的是同一套 classify→authorize，
所以"预演结论 == 执行结论"，不会漂移。analyze 绝不签发确认令牌（令牌只在执行时给）。

被 scripts/ops_analyze.py（CLI）与 mcp_server/server.py 的 analyze 工具共同复用。
"""
from __future__ import annotations

import re
from typing import Any

from dbconnector import levels
from dbconnector.audit import _shape
from mcp_server import guard

_WHERE_RE = re.compile(r"\bWHERE\b", re.I)


def normalize_input(source: str | None, *, sql=None, command=None, cmd_args=None,
                    collection=None, mongo_op=None, pipeline=None) -> tuple[str, dict]:
    """把工具级入参归一成 (op_name, args) —— 与连接器 classify 的口径一致。"""
    if sql is not None:
        op = "query" if guard.is_read_only(sql) else "execute"
        return op, {"sql": sql}
    if command is not None:
        return "command", {"name": command, "args": cmd_args or []}
    # mongo
    if pipeline is not None:
        return "aggregate", {"collection": collection, "pipeline": pipeline}
    _map = {"insert": "insert_one", "insert_many": "insert_many",
            "update": "update_one", "delete": "delete"}
    return _map.get((mongo_op or "").lower(), mongo_op or "find"), {"collection": collection}


def _sql_preview(sql: str, max_rows: int) -> dict:
    head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    ro = guard.is_read_only(sql)
    preview = guard.ensure_limit(sql, max_rows) if ro else sql.strip()
    return {
        "head": head,
        "read_only": ro,
        "multi_statement": guard.has_multiple_statements(sql),
        "where_present": bool(_WHERE_RE.search(sql)) if head in ("UPDATE", "DELETE") else None,
        "executed_preview": preview[:300],
    }


def build_card(conn, src, op: str, args: dict) -> dict:
    """核心：产决策卡。只调 classify/authorize（纯函数，不连库、不发令牌）。"""
    level, target = conn.classify(op, args)
    verdict, reason, level = conn.authorize(src.access, op, args)
    level_name = levels.level_name(level)

    recommendation = {"allow": "auto-execute", "confirm": "needs-human-confirmation",
                      "deny": "blocked"}[verdict]

    card: dict[str, Any] = {
        "intent": {"source": src.name, "dialect": src.config.dialect,
                   "family": getattr(conn, "data_model", None), "op": op, "target": target},
        "risk": {"level": level, "level_name": level_name,
                 "tier_meaning": {0: "读", 1: "数据写", 2: "结构改", 3: "破坏性/不可逆", 4: "管理员"}.get(level, "")},
        "authorize": {"decision": verdict, "matched": reason,
                      "source_grant": levels.level_name(src.access.grant_max),
                      "source_confirm_from": levels.level_name(src.access.confirm_from)},
        "recommendation": recommendation,
        "next": _next_action(conn, op, args, verdict),
        # 明确：分析阶段不给确认令牌
        "note": "analyze 仅预演，不签发确认令牌；需确认的操作请在执行时按提示走二次确认。",
    }

    # 家族预览 + 安全提示
    safety: list[str] = []
    sql = args.get("sql")
    if sql is not None:
        pv = _sql_preview(sql, src.max_rows)
        card["preview"] = pv
        if pv["multi_statement"]:
            safety.append("多语句拼接，执行层会拒绝——请一次一条。")
        if pv["head"] in ("UPDATE", "DELETE") and pv["where_present"] is False:
            safety.append("无 WHERE 的全表 UPDATE/DELETE，已按破坏性处理；建议加 WHERE 限定范围。")
        if level == levels.DESTRUCTIVE:
            safety.append("破坏性/不可逆操作。")
    elif op == "command":
        card["preview"] = {"command": args.get("name"), "args_shape": _shape(args.get("args"))}
        if level >= levels.DESTRUCTIVE:
            safety.append("该 Redis 命令属破坏/管理级。")
    else:
        card["preview"] = {"collection": args.get("collection"),
                           "pipeline": (_shape(args.get("pipeline")) if args.get("pipeline") else None)}
        if level == levels.DESTRUCTIVE:
            safety.append("含 $out/$merge 或删除，按破坏性处理。")
    card["safety_notes"] = safety
    card["human_summary"] = _human_summary(card)
    return card


def _next_action(conn, op, args, verdict):
    if verdict == "allow":
        return {"do": "execute", "hint": "该环境 grant 内，可直接调用对应执行工具"}
    if verdict == "confirm":
        return {"do": "ask_user_then_execute", "hint": "把风险讲清→用户同意→用相同参数调用执行工具，会返回一次性确认令牌，再带 confirm 重发"}
    return {"do": "stop", "hint": "该操作被授权规则拒绝；如需放行请调整该环境的 access(grant/黑白名单)，或换更合适的只读替代"}


def _human_summary(card) -> str:
    i, r, a = card["intent"], card["risk"], card["authorize"]
    return (f"在源 {i['source']}({i['dialect']}) 上执行 {i['op']}"
            f"{('@' + str(i['target'])) if i['target'] else ''} → 风险 {r['level_name']} · 判定 {a['decision']}"
            f"（{a['matched'] or '命中授权'}）")


def decision_brief(conn, src, op: str, args: dict) -> dict:
    """精简决策摘要，供"每次写操作无条件审计"使用（不连库、不发令牌）。"""
    level, target = conn.classify(op, args)
    verdict, reason, level = conn.authorize(src.access, op, args)
    brief = {"op": op, "target": target, "level": levels.level_name(level),
             "decision": verdict, "why": reason or "命中授权",
             "grant": levels.level_name(src.access.grant_max),
             "confirm_from": levels.level_name(src.access.confirm_from)}
    sql = args.get("sql")
    if sql is not None:
        head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        brief["read_only"] = guard.is_read_only(sql)
        brief["multi_statement"] = guard.has_multiple_statements(sql)
        if head in ("UPDATE", "DELETE"):
            brief["where_present"] = bool(_WHERE_RE.search(sql))
    elif op == "command":
        brief["command"] = args.get("name")
    elif args.get("pipeline") is not None:
        brief["has_out_stage"] = not guard.mongo_pipeline_read_only(args["pipeline"])
    return brief
