"""根层访问控制：权限对象 Access + 决策 decide —— 与 MCP/传输无关、与具体方言无关。

一套分级授权流程的中枢。连接器/模板产出 (op, level, target)，decide(access, level, target)
给出 allow/deny/confirm。使用层（MCP）只在其上加"一次性确认令牌"。

Access 只有两个权限旋钮（清楚、不打架）：
  - grant        该环境允许触达的最高操作级（硬上限；ADMIN 命令无论如何都拒绝）
  - confirm_from 从哪一级起需要确认令牌（默认 = grant+1，即"到顶也不用确认"）
判定：level < confirm_from → 放行；confirm_from ≤ level ≤ grant → 需确认；level > grant → 拒绝。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Optional

from . import levels


@dataclass
class Access:
    read: bool = True
    grant_max: int = levels.READ           # 硬上限（含确认最多到 grant_max）
    confirm_from: int = levels.DESTRUCTIVE + 1   # ≥该级的写操作需确认；默认永不
    write_allow: Optional[list] = None     # 写白名单（glob）；None=不启用
    write_deny: list = field(default_factory=list)

    @property
    def allow_ceiling(self) -> int:
        return self.grant_max

    @property
    def allow_write(self) -> bool:         # 兼容旧字段（是否有写权限）
        return self.grant_max >= levels.WRITE_DATA

    # ---- 构造 ----
    @classmethod
    def from_dict(cls, d: dict) -> "Access":
        gm = levels.grant_max(d.get("grant", "read"))
        # confirm_from：新写法优先
        if "confirm_from" in d:
            confirm_from = levels.grant_max(d["confirm_from"])
        elif "confirm_above" in d:                      # 旧：>confirm_above 需确认
            confirm_from = levels.grant_max(d["confirm_above"]) + 1
        else:
            confirm_from = gm + 1                        # 默认：到顶也不确认
        # 旧 allow_escalation：grant 是"免确认上限"，可越到破坏性(需确认)。等价转成新两旋钮。
        if d.get("allow_escalation"):
            no_confirm_ceiling = gm
            gm = max(gm, levels.DESTRUCTIVE)
            if "confirm_from" not in d and "confirm_above" not in d:
                confirm_from = no_confirm_ceiling + 1
        return cls(read=bool(d.get("read", True)), grant_max=gm, confirm_from=confirm_from,
                   write_allow=d.get("write_allow"), write_deny=d.get("write_deny") or [])

    @classmethod
    def from_legacy(cls, allow_write: bool) -> "Access":
        """无 access 时的兼容映射。"""
        gm = levels.DESTRUCTIVE if allow_write else levels.READ
        return cls(read=True, grant_max=gm, confirm_from=gm + 1)


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
    if level > access.grant_max:
        return ("deny", f"操作等级 {levels.level_name(level)} 超出该环境最高授权 {levels.level_name(access.grant_max)}")
    if level >= access.confirm_from:
        return ("confirm", f"操作等级 {levels.level_name(level)} 达到确认阈值 {levels.level_name(access.confirm_from)}，需显式确认")
    return ("allow", "")
