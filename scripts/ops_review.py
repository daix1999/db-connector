"""操作复盘 CLI：读审计 JSONL，按风险级/源/判定/层/时间过滤并聚合成人话摘要。

数据来自 layer=decision 的写操作决策记录（每条带 level/decision/target/op）+ 可选其它层。

示例：
    python scripts/ops_review.py                       # 全量概览
    python scripts/ops_review.py --min-level WRITE_DATA          # 只看数据写及以上
    python scripts/ops_review.py --level DESTRUCTIVE --source mysql8-prod
    python scripts/ops_review.py --decision deny                  # 只看被拒的
    python scripts/ops_review.py --group-by source                # 按环境分组
    python scripts/ops_review.py --min-level WRITE_DATA --html report.html   # 导出 HTML 报表
"""
from __future__ import annotations

import argparse
import collections
import html
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


def scope_default(recs, layer):
    """未显式指定 --layer 时默认只看 decision 写决策记录；没有则回退全部。"""
    if layer:
        return recs
    dec = [r for r in recs if r.get("layer") == "decision"]
    return dec or recs


def apply_filters(recs, *, level=None, min_level=None, source=None, decision=None,
                  layer=None, since=None, op=None):
    out = []
    for r in recs:
        if layer and r.get("layer") != layer:
            continue
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

    lines = [f"共 {len(dec)} 条记录"]
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


_GROUPERS = {
    "level": _lvl_name,
    "decision": lambda r: r.get("decision") or r.get("outcome"),
    "source": _rec_source,
    "dialect": lambda r: r.get("dialect"),
    "family": lambda r: r.get("data_model"),
    "op": lambda r: r.get("op"),
    "layer": lambda r: r.get("layer"),
}
GROUP_KEYS = list(_GROUPERS)
_DEC_ORDER = ["allow", "confirm", "deny", "ok", "error", "denied"]


def _dec_of(r):
    return r.get("decision") or r.get("outcome") or "—"


def group_records(recs, gk="level"):
    """按 gk 分组，每组给总数 + 各判定细分。返回 [(组名, 总数, {判定:数})]，按总数降序。"""
    fn = _GROUPERS.get(gk, _GROUPERS["level"])
    groups = collections.defaultdict(collections.Counter)
    for r in recs:
        key = fn(r) or "—"
        groups[key]["__total__"] += 1
        groups[key][_dec_of(r)] += 1
    ordered = sorted(groups.items(), key=lambda kv: -kv[1]["__total__"])
    out = []
    for name, ctr in ordered:
        detail = {k: v for k, v in ctr.items() if k != "__total__"}
        out.append((name, ctr["__total__"], detail))
    return out


def _pct(part, total):
    return f"{part}/{total} ({round(part / total * 100)}%)" if total else "0"


def to_html(recs, *, title="db-connector 操作复盘", meta="", group_by="level") -> str:
    """生成自包含 HTML 报表：概览卡 + group_by 汇总表 + 明细表。"""
    dec = [r for r in recs if r.get("layer") == "decision"] or recs
    total = len(dec)
    by_dec = collections.Counter(_dec_of(r) for r in dec)
    by_lvl = collections.Counter(_lvl_name(r) for r in dec if _lvl_name(r))
    lvl_badge = {"READ": "#2b6cb0", "WRITE_DATA": "#2f855a", "WRITE_SCHEMA": "#b7791f",
                 "DESTRUCTIVE": "#c53030", "ADMIN": "#742a2a"}

    cards = "".join(
        f'<div class="card"><div class="n">{by_dec.get(d, 0)}</div><div class="l">{d}</div></div>'
        for d in ["allow", "confirm", "deny"] if d in by_dec or True)
    lvl_rows = "".join(
        f'<tr><td><span class="b" style="background:{lvl_badge.get(k, "#555")}">{html.escape(str(k))}</span></td>'
        f'<td>{v}</td><td>{_pct(v, total)}</td></tr>'
        for k, v in sorted(by_lvl.items(), key=lambda kv: -_NAME2INT.get(kv[0], 9)))

    gb_rows = ""
    for name, n, detail in group_records(dec, group_by):
        dd = " ".join(f'{k}:{v}' for k, v in sorted(detail.items(), key=lambda kv: _DEC_ORDER.index(kv[0]) if kv[0] in _DEC_ORDER else 99))
        gb_rows += f"<tr><td>{html.escape(str(name))}</td><td>{n}</td><td>{html.escape(dd)}</td></tr>"

    rows = "".join(
        "<tr><td>{ts}</td><td>{src}</td><td>{dl}</td><td>{op}</td><td>{tgt}</td>"
        "<td><span class=\"b\" style=\"background:{col}\">{lvl}</span></td><td>{dc}</td><td class=\"why\">{why}</td></tr>".format(
            ts=html.escape((r.get("ts") or "")[:19]), src=html.escape(str(_rec_source(r) or "—")),
            dl=html.escape(str(r.get("dialect") or "—")), op=html.escape(str(r.get("op") or "—")),
            tgt=html.escape(str(r.get("target") or "—")),
            col=lvl_badge.get(_lvl_name(r), "#555"), lvl=html.escape(str(_lvl_name(r) or "—")),
            dc=html.escape(_dec_of(r)),
            why=html.escape(str(r.get("why") or "")) +
                (" ·无WHERE" if r.get("where_present") is False else "") +
                (" ·多语句" if r.get("multi_statement") else ""))
        for r in reversed(dec))

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font:14px/1.5 -apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#1a202c}}
.wrap{{max-width:1080px;margin:24px auto;padding:0 16px}}
h1{{font-size:20px;margin:0 0 4px}} .meta{{color:#718096;margin-bottom:16px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:12px 18px;min-width:96px}}
.card .n{{font-size:24px;font-weight:600}} .card .l{{color:#718096}}
h2{{font-size:15px;margin:20px 0 8px;color:#2d3748}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #edf2f7}} th{{background:#f1f5f9;font-weight:600;color:#4a5568}}
.b{{color:#fff;padding:1px 7px;border-radius:10px;font-size:12px}} .why{{color:#718096;font-size:12px}}
</style></head><body><div class="wrap">
<h1>{html.escape(title)}</h1><div class="meta">{html.escape(meta)}</div>
<div class="cards">{cards}</div>
<h2>按风险级（共 {total}）</h2><table><tr><th>风险级</th><th>数量</th><th>占比</th></tr>{lvl_rows}</table>
<h2>按 {html.escape(group_by)} 分组</h2><table><tr><th>{html.escape(group_by)}</th><th>数量</th><th>判定细分</th></tr>{gb_rows}</table>
<h2>明细（最近在前，共 {total}）</h2>
<table><tr><th>时间</th><th>来源</th><th>方言</th><th>操作</th><th>目标</th><th>风险级</th><th>判定</th><th>说明</th></tr>{rows}</table>
</div></body></html>"""


def main() -> int:
    p = argparse.ArgumentParser(description="db-connector 操作复盘（支持按风险级过滤）")
    p.add_argument("--log", default=os.environ.get("DB_AUDIT_LOG") or os.path.join(ROOT, "logs", "db-connector-audit.jsonl"))
    p.add_argument("--level", help="精确风险级 READ|WRITE_DATA|WRITE_SCHEMA|DESTRUCTIVE|ADMIN 或 0-4")
    p.add_argument("--min-level", help="风险级下限（含）")
    p.add_argument("--source", help="按环境名过滤")
    p.add_argument("--decision", choices=["allow", "confirm", "deny", "ok", "error"])
    p.add_argument("--layer", choices=["decision", "mcp", "connector"],
                   help="限定层；缺省时复盘默认只看 decision 写决策记录（更干净）")
    p.add_argument("--op", help="按操作名过滤，如 execute")
    p.add_argument("--since", help="ISO 时间下限，如 2026-09-22T00:00")
    p.add_argument("--limit", type=int, default=30, help="时间线条数（0=不显示）")
    p.add_argument("--group-by", choices=GROUP_KEYS, default="level", help="分组维度（概览/HTML 汇总）")
    p.add_argument("--html", metavar="PATH", help="导出自包含 HTML 报表到该路径")
    p.add_argument("--title", default="db-connector 操作复盘", help="HTML 报表标题")
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
    flt = scope_default(flt, a.layer)      # 默认只看 decision 写决策记录

    if a.json:
        print(json.dumps(flt, ensure_ascii=False, indent=2))
        return 0

    scope = []
    if level is not None: scope.append(f"level={levels.level_name(level)}")
    if min_level is not None: scope.append(f"level>={levels.level_name(min_level)}")
    if a.source: scope.append(f"source={a.source}")
    if a.decision: scope.append(f"decision={a.decision}")
    if a.layer: scope.append(f"layer={a.layer}")
    scope_s = ("  过滤[" + " ".join(scope) + "]") if scope else ""

    if a.html:
        meta = f"来源 {os.path.basename(a.log)} · 记录 {len(flt)}/{len(recs)}{(' · 过滤 ' + ' '.join(scope)) if scope else ''}"
        doc = to_html(flt, title=a.title, meta=meta, group_by=a.group_by)
        out = os.path.abspath(a.html)
        with open(out, "w", encoding="utf-8") as f:
            f.write(doc)
        print(f"已生成 HTML 报表：{out}  （{len(flt)} 条）")
        return 0

    head = f"复盘 {os.path.basename(a.log)}  记录 {len(flt)}/{len(recs)}" + scope_s
    print(head)
    if not flt:
        print("（无匹配记录）"); return 0
    print(summarize(flt))
    # 按 --group-by 维度分组（默认按风险级）
    if a.group_by:
        print(f"\n按 {a.group_by} 分组:")
        for name, n, detail in group_records(flt, a.group_by):
            dd = " ".join(f"{k}:{v}" for k, v in sorted(detail.items(),
                        key=lambda kv: _DEC_ORDER.index(kv[0]) if kv[0] in _DEC_ORDER else 99))
            print(f"  {str(name):<16} {n:>3}   {dd}")
    if a.limit:
        print("\n时间线（最近）:")
        print(timeline(flt, a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
