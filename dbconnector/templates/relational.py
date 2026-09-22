"""关系型数据模型模板（SQL 族）。

任何关系型方言（MySQL / PostgreSQL / SQL Server / Oracle / SQLite / MariaDB / ClickHouse
的 SQL 接口 / TiDB / 国产达梦人大金仓 等）继承本类，只需实现三处：
    dbapi（DB-API 2.0 驱动模块）、_connect_kwargs（连接参数）、可选 _creator_name。
连接池、参数化查询、批量、事务、健康检查、通用探查(list/describe/get_source) 均由本模板提供。

设计约定：本模板"能力全开"（query + execute + 事务），不含任何只读判断；
权限裁剪由使用层（如 mcp_server/guard.py）按运行时配置负责。
"""
from __future__ import annotations

import threading
import time
from abc import abstractmethod
from contextlib import contextmanager
from typing import Any

from .. import audit, levels
from ..base import BaseConnector
from ..config import ConnectorConfig
from ..exceptions import ConnectionError_, QueryError
from ..result import Result, ResultSet


class RelationalConnector(BaseConnector):
    """关系型族基类（DB-API 2.0 + DBUtils 连接池）。"""

    data_model = "relational"
    #: 方言占位符风格，供上层文档/转换参考
    placeholder = "%s"
    #: 需自动审计的操作（fetch_one/fetch_value 会转成 query，不重复登记）
    #: transaction 不在此列——由 transaction() 内部手动做逐条+汇总审计，避免重复/失真
    AUDITED_OPS = ("query", "execute", "execute_returning_id", "execute_many",
                   "list_sources", "describe_source", "get_source")
    #: 操作 → 风险等级（execute 类按 SQL 内容动态判，见 classify）
    OP_LEVELS = {"query": 0, "list_sources": 0, "describe_source": 0, "get_source": 0,
                 "fetch_one": 0, "fetch_value": 0, "ping": 0,
                 "execute": 1, "execute_returning_id": 1, "execute_many": 1, "transaction": 1}

    def classify(self, op: str, args: dict | None = None):
        args = args or {}
        if op in ("execute", "execute_returning_id", "execute_many"):
            sql = args.get("sql", "")
            ts = levels.sql_targets(sql)
            return levels.classify_sql(sql), (ts[0] if ts else args.get("target"))
        return super().classify(op, args)

    def __init__(self, config: ConnectorConfig):
        super().__init__(config)
        self._pool: Any = None
        self._lock = threading.Lock()

    # ---- 子类实现 ----
    @property
    @abstractmethod
    def dbapi(self):
        """返回 DB-API 2.0 驱动模块。"""

    @abstractmethod
    def _connect_kwargs(self) -> dict[str, Any]:
        ...

    def _creator_name(self) -> str:
        return "connect"

    # ---- 连接池 ----
    def ensure_ready(self) -> None:
        self._ensure_pool()

    def _ensure_pool(self):
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is None:
                try:
                    from dbutils.pooled_db import PooledDB
                except ImportError as e:  # pragma: no cover
                    raise ConnectionError_("需要 DBUtils：pip install dbutils") from e
                p = self.config.pool
                self._pool = PooledDB(
                    self.dbapi, mincached=p.mincached, maxcached=p.maxcached,
                    maxconnections=p.maxconnections, blocking=p.blocking,
                    maxusage=p.max_usage, ping=p.ping, **self._connect_kwargs(),
                )
        return self._pool

    def _acquire(self):
        return self._ensure_pool().connection()

    # 关系型族：一条极简 SELECT 探活（Oracle 等子类可覆盖为 SELECT 1 FROM dual）
    def ping(self) -> bool:
        try:
            self.fetch_value("SELECT 1")
            return True
        except Exception:
            return False

    @contextmanager
    def _cursor(self, commit: bool = True):
        conn = self._acquire()
        try:
            cur = conn.cursor()
            try:
                yield cur
                if commit:
                    conn.commit()
            finally:
                cur.close()
        finally:
            conn.close()

    @staticmethod
    def _read(cur) -> tuple[list[str], list[dict[str, Any]]]:
        if not cur.description:
            return [], []
        cols = [d[0] for d in cur.description]
        rows = [dict(r) if isinstance(r, dict) else dict(zip(cols, r)) for r in cur.fetchall()]
        return cols, rows

    # ---- SQL 能力 ----
    def query(self, sql, params=None) -> ResultSet:
        try:
            with self._cursor(commit=False) as cur:
                cur.execute(sql, params or ())
                cols, rows = self._read(cur)
                return ResultSet(columns=cols, rows=rows, rowcount=len(rows))
        except QueryError:
            raise
        except Exception as e:
            raise QueryError(str(e), sql=sql, cause=e) from e

    def fetch_one(self, sql, params=None):
        return self.query(sql, params).first()

    def fetch_value(self, sql, params=None):
        return self.query(sql, params).scalar

    def execute(self, sql, params=None) -> int:
        try:
            with self._cursor(commit=True) as cur:
                cur.execute(sql, params or ())
                return cur.rowcount
        except QueryError:
            raise
        except Exception as e:
            raise QueryError(str(e), sql=sql, cause=e) from e

    def execute_returning_id(self, sql, params=None):
        try:
            with self._cursor(commit=True) as cur:
                cur.execute(sql, params or ())
                return cur.lastrowid
        except QueryError:
            raise
        except Exception as e:
            raise QueryError(str(e), sql=sql, cause=e) from e

    def execute_many(self, sql, seq_params) -> int:
        try:
            with self._cursor(commit=True) as cur:
                cur.executemany(sql, seq_params)
                return cur.rowcount
        except QueryError:
            raise
        except Exception as e:
            raise QueryError(str(e), sql=sql, cause=e) from e

    @contextmanager
    def transaction(self):
        """事务上下文：块内每条语句逐条审计；收尾再记一条汇总（提交/回滚结果）。"""
        conn = self._acquire()
        cur = conn.cursor()
        tx = _Transaction(cur, self)
        t0 = time.perf_counter()
        outcome, err = "ok", None
        try:
            yield tx
            conn.commit()
        except Exception as e:
            outcome, err = audit._classify(e), e
            try:
                conn.rollback()
            finally:
                raise
        finally:
            audit.log_operation(
                self, "transaction",
                {"statements": tx.stmt_count, "writes": tx.write_count},
                outcome=outcome, dur_ms=round((time.perf_counter() - t0) * 1000, 2), error=err)
            cur.close()
            conn.close()

    # ---- 通用探查契约（默认 MySQL/InnoDB 元数据；其他方言子类覆盖）----
    def list_sources(self) -> Result:
        rs = self.query(
            "SELECT TABLE_NAME AS source_name, TABLE_ROWS AS approx_rows, ENGINE AS storage_engine "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME"
        )
        return Result.from_rows(rs.columns, rs.rows, total=len(rs.rows))

    def describe_source(self, name: str) -> Result:
        rs = self.query(
            "SELECT COLUMN_NAME AS col_name, DATA_TYPE AS data_type, IS_NULLABLE AS nullable, "
            "COLUMN_KEY AS col_key, COLUMN_DEFAULT AS default_value "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s "
            "ORDER BY ORDINAL_POSITION", (name,))
        return Result.from_rows(rs.columns, rs.rows)

    def get_source(self, name: str, limit: int = 20) -> Result:
        safe = "".join(c for c in name if c.isalnum() or c == "_")
        rs = self.query(f"SELECT * FROM `{safe}` LIMIT {int(limit)}")
        return Result.from_rows(rs.columns, rs.rows)


# 兼容旧公共名：DBConnector = 关系型族基类
DBConnector = RelationalConnector


class _Transaction:
    """绑定到事务游标的轻量执行器。块内每条语句发一条 connector 层审计。"""

    _READ_PREFIX = ("SELECT", "SHOW", "DESC", "EXPLAIN", "PRAGMA", "WITH")

    def __init__(self, cursor, owner):
        self._cur = cursor
        self._owner = owner
        self.stmt_count = 0
        self.write_count = 0

    def _audit(self, op, sql, params, outcome, dur_ms, result, err):
        audit.log_operation(self._owner, op, {"sql": sql, "params": params},
                            outcome=outcome, dur_ms=dur_ms, result=result, error=err)

    def execute(self, sql, params=None) -> int:
        t0 = time.perf_counter()
        outcome, err, rc = "ok", None, 0
        try:
            self._cur.execute(sql, params or ())
            rc = self._cur.rowcount
            return rc
        except Exception as e:
            outcome, err = audit._classify(e), e
            raise
        finally:
            self.stmt_count += 1
            head = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
            if head not in self._READ_PREFIX:
                self.write_count += 1
            self._audit("transaction.execute", sql, params, outcome,
                        round((time.perf_counter() - t0) * 1000, 2),
                        None if err else {"affected_rows": rc}, err)

    def query(self, sql, params=None) -> list[dict[str, Any]]:
        t0 = time.perf_counter()
        outcome, err, rows = "ok", None, None
        try:
            self._cur.execute(sql, params or ())
            _, rows = RelationalConnector._read(self._cur)
            return rows
        except Exception as e:
            outcome, err = audit._classify(e), e
            raise
        finally:
            self.stmt_count += 1
            self._audit("transaction.query", sql, params, outcome,
                        round((time.perf_counter() - t0) * 1000, 2),
                        None if err else {"rowcount": len(rows or [])}, err)

    def executemany(self, sql, seq_params) -> int:
        t0 = time.perf_counter()
        outcome, err, rc = "ok", None, 0
        try:
            self._cur.executemany(sql, seq_params)
            rc = self._cur.rowcount
            return rc
        except Exception as e:
            outcome, err = audit._classify(e), e
            raise
        finally:
            self.stmt_count += 1
            self.write_count += 1
            n = len(seq_params) if seq_params else 0
            self._audit("transaction.executemany", sql, {"batch": n}, outcome,
                        round((time.perf_counter() - t0) * 1000, 2),
                        None if err else {"affected_rows": rc}, err)
