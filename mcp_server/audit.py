"""操作审计日志（使用层）。

把每一次经 MCP 的工具调用记成结构化 JSON Lines：谁（source）、做了什么（tool + 参数摘要）、
结果（成功/拒绝/错误）、耗时。用于事后追责与异常排查——"能查到发生了什么"。

原则：
  - 默认记录，写失败不影响主流程（尽力而为）。
  - 默认脱敏：只保留标识符/SQL 文本/参数形状，不写参数值；确需全量设 DB_AUDIT_PARAMS=1。
  - 绝不记录连接凭据（凭据只在配置里，不经工具参数）。

环境变量：
  DB_AUDIT        off/0/false 关闭（默认开启）
  DB_AUDIT_LOG    日志文件路径；默认 <cwd>/logs/db-connector-audit.jsonl
  DB_AUDIT_PARAMS 1 时在日志中包含参数值（敏感，默认关）
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time
from datetime import datetime, timezone

_TRUE = {"1", "true", "yes", "y", "on"}

# 这些参数可能含业务数据值，默认只记形状不记值；置 DB_AUDIT_PARAMS=1 才记值
_SENSITIVE_KEYS = {"params", "payload", "filter", "documents", "args"}
# SQL 文本保留（排查必需），但截断
_MAX_SQL = 300
_MAX_ERR = 300


class AuditLogger:
    def __init__(self, path: str, log_params: bool = False):
        self.path = path
        self.log_params = log_params
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._enabled = True
        except Exception:
            self._enabled = False

    def record(self, event: dict) -> None:
        if not self._enabled:
            return
        try:
            line = json.dumps(event, ensure_ascii=False, default=str)
        except Exception:
            line = json.dumps({"ts": event.get("ts"), "tool": event.get("tool"),
                               "outcome": "audit_serialize_error"})
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass  # 审计绝不能拖垮主调用

    # ---- 参数脱敏 ----
    def _sanitize(self, kwargs: dict) -> dict:
        out = {}
        for k, v in kwargs.items():
            if k == "sql" and isinstance(v, str):
                out[k] = v[:_MAX_SQL]
            elif k in _SENSITIVE_KEYS and not self.log_params:
                out[k] = _shape(v)
            elif k in _SENSITIVE_KEYS and self.log_params:
                out[k] = _trunc(v)
            else:
                out[k] = v if isinstance(v, (str, int, float, bool, type(None))) else _shape(v)
        return out


def _shape(v):
    """只描述形状，不含值。"""
    if isinstance(v, dict):
        return {"$type": "object", "keys": list(v.keys())[:20]}
    if isinstance(v, (list, tuple)):
        return {"$type": "array", "len": len(v)}
    if v is None:
        return None
    return {"$type": type(v).__name__}


def _trunc(v, n=120):
    if isinstance(v, (dict, list, tuple)):
        s = json.dumps(v, ensure_ascii=False, default=str)
        return s[:n] + ("…" if len(s) > n else "")
    return v


_logger: AuditLogger | None = None
_get_lock = threading.Lock()


def get_logger() -> AuditLogger | None:
    global _logger
    enabled = (os.getenv("DB_AUDIT", "on") or "on").lower() not in {"off", "0", "false", "no"}
    if not enabled:
        return None
    with _get_lock:
        if _logger is None:
            path = os.getenv("DB_AUDIT_LOG") or os.path.join(os.getcwd(), "logs", "db-connector-audit.jsonl")
            log_params = (os.getenv("DB_AUDIT_PARAMS", "") or "").lower() in _TRUE
            _logger = AuditLogger(path, log_params)
        return _logger


def audited(tool_name: str):
    """装饰器：包住 MCP 工具，记录一次调用。放在 @mcp.tool() 之下（更贴近被包装函数）。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            logger = get_logger()
            t0 = time.perf_counter()
            outcome, err = "ok", None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as e:
                # ToolError 视为"被拒/失败"，其余为错误
                name = type(e).__name__
                outcome = "denied" if name in {"ToolError", "PermissionError", "ValueError"} else "error"
                err = str(e)[:_MAX_ERR]
                raise
            finally:
                if logger is not None:
                    ev = {
                        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                        "tool": tool_name,
                        "source": kwargs.get("source"),
                        "args": logger._sanitize(kwargs),
                        "outcome": outcome,
                        "dur_ms": round((time.perf_counter() - t0) * 1000, 2),
                    }
                    # 结果体量（用于审计"取/改了多少"），仅从成功返回里读
                    summary = _result_summary(locals().get("result"))
                    if summary:
                        ev["detail"] = summary
                    if err:
                        ev["error"] = err
                    logger.record(ev)
        return wrapper
    return deco


def _result_summary(result):
    if not isinstance(result, dict):
        return None
    for key in ("rowcount", "affected_rows", "returned", "count", "deleted_count",
                "modified_count"):
        if key in result:
            return {key: result[key]}
    if "keys" in result and isinstance(result["keys"], list):
        return {"returned": len(result["keys"])}
    return None
