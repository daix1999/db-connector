"""MCP 工具层审计：复用 dbconnector.audit 核心，layer="mcp"。

与连接器层（layer="connector"）共用同一个 AuditLogger 实例（一把锁、同一个文件），
避免并发下整行交错。MCP 层记录"agent 意图 + 被护栏拦截的尝试"，连接器层记录"真实落库操作"。
"""
from __future__ import annotations

from dbconnector.audit import (  # noqa: F401
    AuditLogger,
    _shape,
    emit,
    get_logger,
    reset,
    sanitize,
)
from dbconnector.audit import audited as _core_audited


def audited(tool_name: str):
    return _core_audited(tool_name, layer="mcp")


__all__ = ["audited", "AuditLogger", "get_logger", "emit", "reset", "_shape", "sanitize"]
