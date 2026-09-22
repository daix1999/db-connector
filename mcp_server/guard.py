"""只读安全护栏 + 标识符校验（纯函数，便于单测）。

策略：agent 默认只读。任何非只读语句都会被拒绝；表名/列名等标识符必须过白名单，
避免把用户输入拼进 SHOW/DESCRIBE 造成注入。
"""
from __future__ import annotations

import fnmatch
import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型引用，避免循环导入
    from mcp_server.config import Access

from dbconnector import levels

# 确认令牌密钥：优先 env，否则进程随机（重启即失效，一次性语义更强）
_CONFIRM_SECRET = (os.getenv("DB_CONFIRM_SECRET") or secrets.token_hex(16)).encode()
_CONFIRM_TTL = int(os.getenv("DB_CONFIRM_TTL", "300"))  # 秒

# 允许的只读起始关键字
_READ_ONLY_HEADS = {"SELECT", "WITH", "SHOW", "DESC", "DESCRIBE", "EXPLAIN"}

# 危险关键字（出现即拒，双保险）
_FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "REPLACE", "GRANT", "REVOKE", "SET", "CALL", "LOAD", "INTO", "HANDLER",
    "LOCK", "UNLOCK", "RENAME", "ANALYZE", "OPTIMIZE", "KILL", "RESET",
    "FLUSH", "PREPARE", "EXECUTE", "DEALLOCATE",
}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_HASH_COMMENT = re.compile(r"#[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def strip_comments(sql: str) -> str:
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    sql = _HASH_COMMENT.sub(" ", sql)
    return sql.strip()


def is_read_only(sql: str) -> bool:
    """判断一条语句是否只读。多语句（用分号拼接多条）一律判为不安全。"""
    s = strip_comments(sql)
    if not s:
        return False
    # 去掉结尾分号后仍有分号 => 多语句，拒绝
    trimmed = s.rstrip().rstrip(";")
    if ";" in trimmed:
        return False
    head = trimmed.lstrip("(").split(None, 1)[0].upper() if trimmed.split() else ""
    if head not in _READ_ONLY_HEADS:
        return False
    # SELECT/WITH 主体里若混入 INTO OUTFILE / 危险词也拒
    upper = trimmed.upper()
    if head in ("SELECT", "WITH"):
        toks = set(re.findall(r"[A-Z_]+", upper))
        if toks & (_FORBIDDEN - {"SET"}):   # SET 在 SELECT 别名场景常见，单列出
            return False
        if "INTO" in toks or "OUTFILE" in toks or "DUMPFILE" in toks:
            return False
    return True


def validate_identifier(name: str) -> str:
    """校验表名/库名等标识符，非法则抛错。"""
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"非法标识符: {name!r}")
    return name


def ensure_limit(sql: str, max_rows: int) -> str:
    """给 SELECT/WITH 语句在无 LIMIT 时补一个 LIMIT，兜底防止拉全表。"""
    s = strip_comments(sql)
    trimmed = s.rstrip().rstrip(";")
    head = trimmed.split(None, 1)[0].upper() if trimmed.split() else ""
    if head in ("SELECT", "WITH") and " LIMIT " not in f" {trimmed.upper()} ":
        return f"{trimmed} LIMIT {int(max_rows)}"
    return s


def has_multiple_statements(sql: str) -> bool:
    """去掉注释与结尾分号后仍含分号 => 拼接了多条语句（危险，写路径一律拒绝）。"""
    trimmed = strip_comments(sql).rstrip().rstrip(";")
    return ";" in trimmed


# ======================================================================
# NoSQL 只读判定（Redis / Mongo）——护栏只在"使用层"生效，连接器本身不限权
# ======================================================================

# Redis 只读命令白名单（大写）。不在名单里的一律视为写/危险命令。
REDIS_READ_COMMANDS = {
    "GET", "MGET", "GETRANGE", "STRLEN", "EXISTS", "TYPE", "SCAN",
    "TTL", "PTTL", "HGET", "HMGET", "HGETALL", "HKEYS", "HVALS", "HLEN", "HEXISTS",
    "HSCAN", "LRANGE", "LLEN", "LINDEX", "SMEMBERS", "SISMEMBER", "SCARD", "SSCAN",
    "SUNION", "SINTER", "SDIFF", "ZRANGE", "ZRANGEBYSCORE", "ZCARD", "ZSCORE", "ZSCAN",
    "DBSIZE", "INFO", "PING", "ECHO", "DUMP",
    "OBJECT", "RANDOMKEY", "TIME", "LOLWUT",
}

# Mongo 只读操作名（连接器方法层面判定）。写/危险操作不在此列。
MONGO_READ_OPS = {"find", "count", "aggregate", "list_sources", "describe_source",
                  "get_source", "list_databases", "estimated_count"}

# 明确危险的 Redis 命令，即便将来放开写也建议二次确认
REDIS_DANGEROUS = {"FLUSHALL", "FLUSHDB", "CONFIG", "SHUTDOWN", "DEBUG", "SAVE",
                   "BGSAVE", "RENAME", "KEYS"}


def redis_read_only_ok(command: str) -> bool:
    return command.upper() in REDIS_READ_COMMANDS


def mongo_read_only_ok(op: str) -> bool:
    return op.lower() in MONGO_READ_OPS


def mongo_pipeline_read_only(pipeline: list) -> bool:
    """聚合管道只读判定：出现 $out / $merge 即视为写。"""
    if not isinstance(pipeline, list):
        return False
    for stage in pipeline:
        if isinstance(stage, dict) and any(k in stage for k in ("$out", "$merge")):
            return False
    return True


# ======================================================================
# 读写分离分级授权：决策已上移到根 dbconnector.acl（所有模板共用一套流程）
# 本模块只保留"一次性确认令牌"（传输层关注）。
# ======================================================================
from dbconnector.acl import Access, decide, _match_any  # noqa: F401  re-export 兼容


def _token_sig(source: str, op: str, level: int, target: Optional[str], exp: int) -> str:
    msg = f"{source}|{op}|{level}|{target or ''}|{exp}".encode()
    return hmac.new(_CONFIRM_SECRET, msg, hashlib.sha256).hexdigest()


def issue_token(source: str, op: str, level: int, target: Optional[str]) -> tuple[str, int]:
    exp = int(time.time()) + _CONFIRM_TTL
    return f"{exp}.{_token_sig(source, op, level, target, exp)}", exp


def verify_token(token: Optional[str], source: str, op: str, level: int,
                 target: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(sig, _token_sig(source, op, level, target, exp))
