"""操作复盘 CLI：读审计 JSONL，按风险级/源/判定/层/时间过滤并聚合成人话摘要。

数据来自 layer=decision 的写操作决策记录（每条带 level/decision/target/op）+ 可选其它层。

示例：
    python scripts/ops_review.py                       # 全量概览
    python scripts/ops_review.py --min-level WRITE_DATA          # 只看数据写及以上
    python scripts/ops_review.py --level DESTRUCTIVE --source mysql8-prod
    python scripts/ops_review.py --decision deny                  # 只看被拒的
    python scripts/ops_review.py --since 2026-09-22T00:00 --limit 50 --json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dbconnector import levels                                   # noqa: E402

_NAME2INT = {v: k for k, v in levels.LEVEL_NAMES.items()}     # 'READ'->0 ...


def _to_level(s: str) -> int:
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    return _NAME2INT[s.upper()]


def load(path: str) -> list[dict]:
    recs = []
    if not os.path.exists(path):
        return recs
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


def _rec_source(r: dict) -> str | None:
    return r.get("label") or r.get("source")


def _rec_level_int(r: dict) -> int | None:
    lv = r.get("level")
    if isinstance(lv, int):
        return lv
    if isinstance(lv, str):
        return _NAME2INT.get(lv.upper())
    return None


def _ts(r: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(r.get("ts", ""))
    except Exception:
        return None


def apply_filters(recs, *, level=None, min_level=None, source=None, decision=None,
                  layer=None, since=None, op=None):
    out = []
    for r in recs:
        if layer and r.get("layer") != layer:
            continue
        if not layer and r.get("layer") != "decision" and not (level or min_level):
            # 不带层过滤且没要求风险级时，复盘聚焦决策记录，避免噪音
            pass
        lv = _rec_level_int(r)
        if level is not None and lv != level:
            continue
        if min_level is not None and (lv is None or lv < min_level):
            continue
        if source and _rec_source(r) != source:
            continue
        if decision and r.get("decision") != decision and r.get("outcome") != decision:
            continue
        if op and r.get("op") != op:
            continue
        if since:
            t = _ts(r)
            if t is None or t < since:
                continue
        out.append(r)
    return out


def summarize(recs) -> str:
    dec = [r for r in recs if r.get("layer") == "decision"] or recs
    by_decision = collections.Counter(r.get("decision") or r.get("outcome") for r in dec)
    by_level = collections.Counter(_lvl_name(r) for r in dec if _lvl_name(r))
    by_source = collections.Counter(_rec_source(r) for r in dec if _rec_source(r))
    by_op = collections.Counter(r.get("op") for r in dec if r.get("op"))

    lines = [f"共 {len(dec)} 条决策记录"]
    def _row(title, ctr, order=None):
        items = sorted(ctr.items(), key=lambda kv: (order.index(kv[0]) if order and kv[0] in order else 99, -kv[1]))
        lines.append(f"  按{title}: " + (" · ".join(f"{k} {v}" for k, v in items if k) or "—"))
    _row("判定", by_decision, ["allow", "confirm", "deny", "ok", "error"])
    _row("风险级", by_level, ["READ", "WRITE_DATA", "WRITE_SCHEMA", "DESTRUCTIVE", "ADMIN"])
    _row("来源", by_source)
    _row("操作", by_op)
    return "\n".join(lines)


def _lvl_name(r):
    lv = _rec_level_int(r)
    return levels.level_name(lv) if lv is not None else None


def timeline(recs, limit):
    dec = [r for r in recs if r.get("layer") == "decision"] or recs
    dec = dec[-limit:] if limit else dec
    rows = []
    for r in dec:
        rows.append("  {ts}  {src:<14} {op:<10} {tgt:<16} {lvl:<13} {dec:<7} {why}".format(
            ts=(r.get("ts") or "")[:19], src=_rec_source(r) or "—", op=r.get("op") or "—",
            tgt=str(r.get("target") or "—")[:16], lvl=_lvl_name(r) or "—",
            dec=r.get("decision") or r.get("outcome") or "—",
            why=(r.get("why") or "")[:40]))
    return "\n".join(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="db-connector 操作复盘（支持按风险级过滤）")
    p.add_argument("--log", default=os.environ.get("DB_AUDIT_LOG") or os.path.join(ROOT, "logs", "db-connector-audit.jsonl"))
    p.add_argument("--level", help="精确风险级 READ|WRITE_DATA|WRITE_SCHEMA|DESTRUCTIVE|ADMIN 或 0-4")
    p.add_argument("--min-level", help="风险级下限（含）")
    p.add_argument("--source", help="按环境名过滤")
    p.add_argument("--decision", choices=["allow", "confirm", "deny", "ok", "error"])
    p.add_argument("--layer", choices=["decision", "mcp", "connector"])
    p.add_argument("--op", help="按操作名过滤，如 execute")
    p.add_argument("--since", help="ISO 时间下限，如 2026-09-22T00:00")
    p.add_argument("--limit", type=int, default=30, help="时间线条数（0=不显示）")
    p.add_argument("--json", action="store_true", help="输出过滤后的原始记录 JSON")
    a = p.parse_args()

    recs = load(a.log)
    if not recs:
        print(f"日志为空或不存在：{a.log}", file=sys.stderr)
        return 1

    level = _to_level(a.level) if a.level else None
    min_level = _to_level(a.min_level) if a.min_level else None
    since = datetime.fromisoformat(a.since) if a.since else None

    flt = apply_filters(recs, level=level, min_level=min_level, source=a.source,
                        decision=a.decision, layer=a.layer, since=since, op=a.op)

    if a.json:
        print(json.dumps(flt, ensure_ascii=False, indent=2))
        return 0

    scope = []
    if level is not None: scope.append(f"level={levels.level_name(level)}")
    if min_level is not None: scope.append(f"level>={levels.level_name(min_level)}")
    if a.source: scope.append(f"source={a.source}")
    if a.decision: scope.append(f"decision={a.decision}")
    if a.layer: scope.append(f"layer={a.layer}")
    head = f"复盘 {os.path.basename(a.log)}  记录 {len(flt)}/{len(recs)}"
    if scope: head += "  过滤[" + " ".join(scope) + "]"
    print(head)
    if not flt:
        print("（无匹配记录）"); return 0
    print(summarize(flt))
    if a.limit:
        print("时间线（最近）:")
        print(timeline(flt, a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
