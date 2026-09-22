"""操作决策分析 CLI（纯静态，不连库、不发确认令牌）。

读当前 DB_SOURCES / DB_ACCESS_PROFILE_FILE 配置，把一个"具体操作"跑成决策卡：
意图/风险级/授权判定(allow|confirm|deny)/执行预览/建议下一步。判定与执行同源。

示例：
    python scripts/ops_analyze.py --list-sources
    python scripts/ops_analyze.py --source mysql8-prod --sql "UPDATE t SET x=1"
    python scripts/ops_analyze.py --source cache --command DEL --args user:1
    python scripts/ops_analyze.py --source docs --collection orders --op delete --filter-json '{"a":1}'
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dbconnector import create                                   # noqa: E402
from mcp_server import analyzer                                  # noqa: E402
from mcp_server.config import load_settings                      # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="db-connector 操作决策分析（静态预演）")
    p.add_argument("--list-sources", action="store_true", help="列出已配置环境与权限档")
    p.add_argument("--source", help="目标环境名（见 --list-sources）")
    p.add_argument("--sql", help="关系型：一条 SQL")
    p.add_argument("--command", help="键值：Redis 命令名")
    p.add_argument("--args", nargs="*", default=None, help="命令参数（如 key）")
    p.add_argument("--collection", help="文档：集合名")
    p.add_argument("--op", help="文档：find/insert/update/delete/aggregate")
    p.add_argument("--pipeline-json", help="文档：聚合管道 JSON")
    a = p.parse_args()

    settings = load_settings()

    if a.list_sources or not a.source:
        rows = [{"name": n, "dialect": s.config.dialect, "database": s.config.database,
                 "grant": _gl(s.access.grant_max), "confirm_from": _gl(s.access.confirm_from),
                 "write_allow": s.access.write_allow, "write_deny": s.access.write_deny}
                for n, s in settings.sources.items()]
        print(json.dumps({"sources": rows}, ensure_ascii=False, indent=2))
        if not a.source:
            return 0

    if a.source not in settings.sources:
        print(f"未知 source：{a.source}；可用：{list(settings.sources)}", file=sys.stderr)
        return 2
    src = settings.sources[a.source]
    conn = create(src.config)          # 只建对象，不连库

    op, args = analyzer.normalize_input(
        a.source, sql=a.sql, command=a.command, cmd_args=a.args,
        collection=a.collection, mongo_op=a.op,
        pipeline=json.loads(a.pipeline_json) if a.pipeline_json else None)
    if op == "find" and a.collection is None and not a.sql and not a.command:
        print("请用 --sql / --command / --collection(+--op) 指定要分析的操作", file=sys.stderr)
        return 2

    card = analyzer.build_card(conn, src, op, args)
    print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0


def _gl(n):
    from dbconnector import levels
    return levels.level_name(n)


if __name__ == "__main__":
    sys.exit(main())
