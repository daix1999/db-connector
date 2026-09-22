"""根层访问控制：权限对象 Access + 决策 decide —— 与 MCP/传输无关。

这是"一套分级授权流程"的中枢：连接器/模板产出 (op, level, target)，
decide(access, level, target) 给出 allow/deny/confirm。使用层（MCP）只在其上
加确认令牌的签发/校验，不再各自实现判级/判定。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Optional

from . import levels


@dataclass
class Access:
    """某源的授权配置。"""
    read: bool = True
    grant_max: int = levels.READ          # 免确认可达最高级
    write_allow: Optional[list] = None    # None=不设白名单；给定=仅这些目标可写
    write_deny: list = field(default_factory=list)
    confirm_above: int = levels.ADMIN     # >该级需确认（默认 ADMIN 表示不额外要求）
    allow_escalation: bool = False        # 是否允许确认令牌越权（上限=破坏性）

    @property
    def allow_ceiling(self) -> int:
        return min(self.grant_max, self.confirm_above)

    # 兼容旧字段
    @property
    def allow_write(self) -> bool:
        return self.grant_max >= levels.WRITE_DATA

    @classmethod
    def from_dict(cls, d: dict) -> "Access":
        grant = levels.grant_max(d.get("grant", "read"))
        confirm = d.get("confirm_above")
        confirm = grant if confirm is None else levels.grant_max(confirm)
        return cls(read=bool(d.get("read", True)), grant_max=grant,
                   write_allow=d.get("write_allow"), write_deny=d.get("write_deny") or [],
                   confirm_above=confirm, allow_escalation=bool(d.get("allow_escalation", False)))

    @classmethod
    def from_legacy(cls, allow_write: bool) -> "Access":
        """无 access 时的兼容映射。"""
        grant = levels.DESTRUCTIVE if allow_write else levels.READ
        return cls(read=True, grant_max=grant, confirm_above=grant, allow_escalation=False)


def _match_any(target: Optional[str], patterns) -> bool:
    if not patterns or target is None:
        return False
    t = str(target).lower()
    return any(fnmatch.fnmatch(t, str(p).lower()) for p in patterns)


def decide(access: Access, level: int, target: Optional[str]) -> tuple[str, str]:
    """统一决策。返回 (verdict, reason)，verdict ∈ allow|confirm|deny。"""
    if level == levels.READ:
        return ("allow", "") if access.read else ("deny", "该源未授权读")
    if level >= levels.ADMIN:
        return ("deny", "管理员级操作(FLUSHALL/CONFIG/SHUTDOWN/dropDatabase…)永久拒绝")
    if _match_any(target, access.write_deny):
        return ("deny", f"目标 {target!r} 命中写黑名单")
    if access.write_allow is not None and not _match_any(target, access.write_allow):
        return ("deny", f"目标 {target!r} 不在写白名单 {access.write_allow}")
    if level <= access.allow_ceiling:
        return ("allow", "")
    if level <= levels.DESTRUCTIVE and access.allow_escalation:
        return ("confirm", f"操作风险等级 {levels.level_name(level)} 超出该源免确认上限 "
                           f"{levels.level_name(access.allow_ceiling)}，需显式确认")
    return ("deny", f"操作等级 {levels.level_name(level)} 超出授权 {levels.level_name(access.allow_ceiling)}")
