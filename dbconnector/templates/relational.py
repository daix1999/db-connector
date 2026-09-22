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
from abc import abstractmethod
from contextlib import contextmanager
from typing import Any

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
    AUDITED_OPS = ("query", "execute", "execute_returning_id", "execute_many",
                   "transaction", "list_sources", "describe_source", "get_source")

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
        conn = self._acquire()
        cur = conn.cursor()
        try:
            yield _Transaction(cur)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            finally:
                raise
        finally:
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
    def __init__(self, cursor):
        self._cur = cursor

    def execute(self, sql, params=None) -> int:
        self._cur.execute(sql, params or ())
        return self._cur.rowcount

    def query(self, sql, params=None) -> list[dict[str, Any]]:
        self._cur.execute(sql, params or ())
        _, rows = RelationalConnector._read(self._cur)
        return rows

    def executemany(self, sql, seq_params) -> int:
        self._cur.executemany(sql, seq_params)
        return self._cur.rowcount
