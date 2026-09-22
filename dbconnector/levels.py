"""操作风险分级（客观分类，不含策略）。

把一次"操作"按可逆性/影响面归到 5 级。分级是客观事实（DROP 在哪都属破坏性），
策略（允不允许、要不要确认）在使用层依据 grant/白名单/确认门 来判。

  T0 READ         读：SELECT/SHOW/DESC/EXPLAIN、Redis 只读命令、Mongo find/count
  T1 WRITE_DATA   数据写：INSERT/UPDATE(带WHERE)/DELETE(带WHERE)、Redis 写、Mongo insert/update
  T2 WRITE_SCHEMA 结构改（非破坏）：CREATE/ALTER/建索引/建集合
  T3 DESTRUCTIVE  破坏性（不可逆）：DROP/TRUNCATE/无WHERE的DELETE/FLUSHDB/drop collection
  T4 ADMIN        服务器级：CONFIG/SHUTDOWN/GRANT/REVOKE/dropDatabase —— 上层通常永久拒绝

grant 名称映射到"默认可直达的最高级"：
  read -> 0 | read+data -> 1 | read+schema -> 2 | read+destructive -> 3 | admin -> 4
"""
from __future__ import annotations

import re

READ, WRITE_DATA, WRITE_SCHEMA, DESTRUCTIVE, ADMIN = 0, 1, 2, 3, 4
LEVEL_NAMES = {0: "READ", 1: "WRITE_DATA", 2: "WRITE_SCHEMA", 3: "DESTRUCTIVE", 4: "ADMIN"}

_GRANT_TO_MAX = {
    "read": READ, "ro": READ,
    "read+data": WRITE_DATA, "rw": WRITE_DATA,
    "read+schema": WRITE_SCHEMA,
    "read+destructive": DESTRUCTIVE, "rwd": DESTRUCTIVE,
    "admin": ADMIN,
}


def grant_max(grant: str) -> int:
    """把 grant 名解析成最高可达等级；未知按 READ（最安全）。"""
    return _GRANT_TO_MAX.get((grant or "read").strip().lower(), READ)


_READ_HEADS = ("SELECT", "SHOW", "DESC", "DESCRIBE", "EXPLAIN", "WITH", "PRAGMA")
_ADMIN_HEADS = {"GRANT", "REVOKE", "SHUTDOWN", "KILL", "SET", "RESET", "CONFIG"}
_DESTRUCTIVE_HEADS = {"DROP", "TRUNCATE", "RENAME", "FLUSHDB"}
_SCHEMA_HEADS = {"CREATE", "ALTER"}

_TABLE_RE = re.compile(
    r"\b(?:FROM|INTO|UPDATE|TABLE|JOIN)\s+[`\"]?([A-Za-z_][\w.]*)", re.I)


def classify_sql(sql: str) -> int:
    """按语句首关键词(去掉注释/前导括号)客观分级，避免 UPDATE...SET 里的 SET 被误判为 ADMIN。"""
    s = sql.upper()
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)          # 去块注释
    s = re.sub(r"--[^\n]*", " ", s)                        # 去行注释
    s = s.strip().lstrip("(").strip()
    words = s.split()
    head = words[0] if words else ""
    padded = f" {s} "

    if head in _READ_HEADS:
        # SELECT ... INTO OUTFILE/DUMPFILE 属导出，视为管理级
        if head == "SELECT" and (" INTO OUTFILE " in padded or " INTO DUMPFILE " in padded):
            return ADMIN
        return READ
    if head in _ADMIN_HEADS:
        return ADMIN
    if head in _DESTRUCTIVE_HEADS:
        return DESTRUCTIVE
    if head in _SCHEMA_HEADS:
        return WRITE_SCHEMA
    if head in ("INSERT", "REPLACE", "UPSERT"):
        return WRITE_DATA
    if head == "UPDATE":
        return WRITE_DATA if " WHERE " in padded else DESTRUCTIVE
    if head == "DELETE":
        return WRITE_DATA if " WHERE " in padded else DESTRUCTIVE
    return WRITE_DATA if head else READ     # 未知语句按写处理（保守）


def sql_targets(sql: str) -> list[str]:
    """尽力从 SQL 抽取涉及的表名（用于白/黑名单匹配）。抽不出则空。"""
    return sorted({m.group(1).split(".")[-1] for m in _TABLE_RE.finditer(sql)})


# Redis：按命令归类
_REDIS_READ = {
    "GET", "MGET", "GETRANGE", "STRLEN", "EXISTS", "TYPE", "SCAN", "TTL", "PTTL",
    "HGET", "HMGET", "HGETALL", "HKEYS", "HVALS", "HLEN", "HEXISTS", "HSCAN",
    "LRANGE", "LLEN", "LINDEX", "SMEMBERS", "SISMEMBER", "SCARD", "SSCAN",
    "SUNION", "SINTER", "SDIFF", "ZRANGE", "ZRANGEBYSCORE", "ZCARD", "ZSCORE",
    "ZSCAN", "DBSIZE", "INFO", "PING", "ECHO", "DUMP", "OBJECT", "RANDOMKEY", "TIME",
}
_REDIS_DATA = {
    "SET", "SETEX", "SETNX", "MSET", "APPEND", "GETSET", "SETRANGE", "INCR", "DECR",
    "INCRBY", "DECRBY", "HSET", "HMSET", "HINCRBY", "LPUSH", "RPUSH", "LSET", "LPOP",
    "RPOP", "SADD", "SREM", "ZADD", "ZREM", "EXPIRE", "PEXPIRE", "PERSIST", "RENAME",
    "GETDEL",
}
_REDIS_DESTRUCTIVE = {"DEL", "UNLINK", "FLUSHDB", "KEYS"}   # KEYS 阻塞性；DEL 破坏性
_REDIS_ADMIN = {"FLUSHALL", "CONFIG", "SHUTDOWN", "DEBUG", "SAVE", "BGSAVE", "CLUSTER"}


def classify_redis(cmd: str) -> int:
    c = cmd.upper()
    if c in _REDIS_ADMIN:
        return ADMIN
    if c in _REDIS_DESTRUCTIVE:
        return DESTRUCTIVE
    if c in _REDIS_DATA:
        return WRITE_DATA
    if c in _REDIS_READ:
        return READ
    return DESTRUCTIVE   # 未知命令按最高风险处理，逼显式授权


# Mongo：按操作名归类
_MONGO_READ = {"find", "count", "aggregate_read", "list_collections", "estimated_count"}
_MONGO_DATA = {"insert_one", "insert_many", "update_one", "update_many", "delete",
               "delete_one", "delete_many", "replace"}
_MONGO_SCHEMA = {"create_collection", "create_index", "drop_index"}
_MONGO_DESTRUCTIVE = {"drop_collection"}
_MONGO_ADMIN = {"drop_database", "command_admin"}


def classify_mongo(op: str, pipeline: list | None = None) -> int:
    o = op.lower()
    if o == "aggregate":
        # 含 $out/$merge 的写型聚合按破坏性；否则读
        if pipeline and any(isinstance(s, dict) and ("$out" in s or "$merge" in s) for s in pipeline):
            return DESTRUCTIVE
        return READ
    if o in _MONGO_ADMIN:
        return ADMIN
    if o in _MONGO_DESTRUCTIVE:
        return DESTRUCTIVE
    if o in _MONGO_SCHEMA:
        return WRITE_SCHEMA
    if o in _MONGO_DATA:
        return WRITE_DATA
    if o in _MONGO_READ:
        return READ
    return WRITE_DATA


def level_name(level: int) -> str:
    return LEVEL_NAMES.get(level, str(level))
