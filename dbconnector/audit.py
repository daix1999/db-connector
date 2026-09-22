"""操作审计核心（库层，无 MCP 依赖）。

定位：审计下沉到连接器（BaseConnector），因此无论经 MCP server 还是直接用本库
connect(...).query(...)，落库操作都会留痕；MCP 层再补记 agent 意图与被护栏拦截的尝试。
两层共用同一个 AuditLogger 实例（一把锁写同一文件），避免并发下整行交错。

记录字段：ts, layer(mcp|connector), source/label, dialect, data_model, op, args(脱敏),
outcome(ok|denied|error), dur_ms, detail(结果量), error。

环境变量：
  DB_AUDIT         off/0/false 关闭（默认开启）
  DB_AUDIT_LAYER   all(默认) | connector | mcp | off —— 分层开关
  DB_AUDIT_LOG     路径；默认 <cwd>/logs/db-connector-audit.jsonl
  DB_AUDIT_PARAMS  1 时记录参数值（敏感，默认只记形状）
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time
from datetime import datetime, timezone

_TRUE = {"1", "true", "yes", "y", "on"}
_SENSITIVE_KEYS = {"params", "payload", "filter", "documents", "args", "seq_params"}
_MAX_SQL, _MAX_ERR = 300, 300


def _shape(v):
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


def sanitize(args: dict, log_params: bool) -> dict:
    out = {}
    for k, v in args.items():
        if k == "sql" and isinstance(v, str):
            out[k] = v[:_MAX_SQL]
        elif k in _SENSITIVE_KEYS and log_params:
            out[k] = _trunc(v)
        elif k in _SENSITIVE_KEYS:
            out[k] = _shape(v)
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = _shape(v)
    return out


class AuditLogger:
    def __init__(self, path: str, log_params: bool = False):
        self.path, self.log_params = path, log_params
        self._lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self.enabled = True
        except Exception:
            self.enabled = False

    def record(self, event: dict) -> None:
        if not self.enabled:
            return
        try:
            line = json.dumps(event, ensure_ascii=False, default=str)
        except Exception:
            line = json.dumps({"ts": event.get("ts"), "op": event.get("op"),
                               "outcome": "audit_serialize_error"})
        with self._lock:  # 全进程唯一锁 => 整行原子写入
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass


_logger: AuditLogger | None = None
_lock = threading.Lock()


def _layer_enabled(layer: str) -> bool:
    sel = (os.getenv("DB_AUDIT_LAYER") or "all").lower()
    if (os.getenv("DB_AUDIT", "on") or "").lower() in {"off", "0", "false", "no"}:
        return False
    return sel in {"all", "both"} or sel == layer


def get_logger() -> AuditLogger | None:
    global _logger
    with _lock:
        if _logger is None:
            path = os.getenv("DB_AUDIT_LOG") or os.path.join(os.getcwd(), "logs", "db-connector-audit.jsonl")
            params = (os.getenv("DB_AUDIT_PARAMS", "") or "").lower() in _TRUE
            _logger = AuditLogger(path, params)
        return _logger


def reset() -> None:
    """清缓存，供测试切换 env 后重建。"""
    global _logger
    with _lock:
        _logger = None


def emit(layer: str, *, identity: dict, op: str, args: dict,
         outcome: str, dur_ms: float, detail=None, error=None) -> None:
    lg = get_logger()
    if lg is None or not _layer_enabled(layer):
        return
    ev = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
          "layer": layer, "op": op, "outcome": outcome, "dur_ms": dur_ms}
    ev.update({k: v for k, v in identity.items() if v is not None})
    ev["args"] = sanitize(args, lg.log_params)
    if detail:
        ev["detail"] = detail
    if error:
        ev["error"] = str(error)[:_MAX_ERR]
    lg.record(ev)


def _result_detail(result):
    if isinstance(result, dict):
        for key in ("rowcount", "affected_rows", "returned", "count", "deleted_count",
                    "modified_count", "inserted_id", "inserted_ids", "dbsize"):
            if key in result:
                val = result[key]
                return {key: (len(val) if isinstance(val, list) else val)}
        if isinstance(result.get("keys"), list):
            return {"returned": len(result["keys"])}
    if isinstance(result, int):
        return {"affected_rows": result}
    return None


def _classify(exc: Exception) -> str:
    return "denied" if type(exc).__name__ in {"ToolError", "PermissionError", "ValueError"} else "error"


def audited(tool_name: str, layer: str = "mcp", identity=None):
    """装饰器：MCP 工具用（layer=mcp）。identity 可为静态 dict 或 callable。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            outcome, err, result = "ok", None, None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as e:
                outcome, err = _classify(e), e
                raise
            finally:
                ident = identity(**{}) if callable(identity) else (identity or {})
                emit(layer, identity={**ident, "source": kwargs.get("source")},
                     op=tool_name, args=kwargs, outcome=outcome,
                     dur_ms=round((time.perf_counter() - t0) * 1000, 2),
                     detail=_result_detail(result), error=err)
        return wrapper
    return deco


def wrap_operation(fn, op_name: str):
    """装饰器：包连接器操作（layer=connector）。identity 从 self 派生。"""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        t0 = time.perf_counter()
        outcome, err, result = "ok", None, None
        try:
            result = fn(self, *args, **kwargs)
            return result
        except Exception as e:
            outcome, err = _classify(e), e
            raise
        finally:
            ident = {"dialect": getattr(self, "dialect", None),
                     "data_model": getattr(self, "data_model", None),
                     "label": getattr(self, "source_label", None) or getattr(self.config, "label", None),
                     "host": self.config.host, "database": self.config.database}
            # 位置参数按函数形参名映射，便于脱敏
            named = _bind_args(fn, self, args, kwargs)
            emit("connector", identity=ident, op=op_name, args=named,
                 outcome=outcome, dur_ms=round((time.perf_counter() - t0) * 1000, 2),
                 detail=_result_detail(result), error=err)
    wrapper._audited = True
    return wrapper


def _bind_args(fn, self, args, kwargs) -> dict:
    import inspect
    try:
        sig = inspect.signature(getattr(fn, "__wrapped__", fn))
        bound = sig.bind(self, *args, **kwargs)
        bound.apply_defaults()
        d = dict(bound.arguments)
        d.pop("self", None)
        return d
    except Exception:
        return dict(kwargs)
