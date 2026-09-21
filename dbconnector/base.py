"""两层抽象。

第一层 BaseConnector：所有后端（关系/键值/文档）共同满足的极简契约
    - 生命周期：ensure_ready / close / ping / health_check / 上下文管理
    - 通用探查：list_sources / describe_source / get_source / query_descriptor

第二层按数据模型分族：
    - RelationalConnector(BaseConnector)：SQL 族，连接池 + query/execute/事务
      （旧名 DBConnector 作为别名保留，MySQL 等关系型零改动继续工作）
    - KeyValueConnector / DocumentConnector：见 kv.py / doc.py，Redis / Mongo 用

设计原则（按头儿要求）：连接器本身"能力全开"，不内置只读限制；
读写权限的裁剪放在使用层（MCP server 的 guard）按运行时开关处理。
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Sequence

from .config import ConnectorConfig
from .exceptions import ConnectionError_, QueryError
from .result import Result, ResultSet


# ======================================================================
# 第一层：公共契约
# ======================================================================
class BaseConnector(ABC):
    """与数据模型无关的最小公共接口。子类只需实现族专属能力 + 下面几个探查方法。"""

    dialect: str = ""
    #: 该方言默认端口，子类可覆盖
    default_port: int | None = None

    def __init__(self, config: ConnectorConfig):
        if config.dialect != self.dialect:
            raise ValueError(f"配置方言 {config.dialect!r} 与连接器 {self.dialect!r} 不匹配")
        self.config = config
        if self.config.port is None:
            self.config.port = self.default_port

    # ---- 生命周期 ----
    @abstractmethod
    def ensure_ready(self) -> None:
        """惰性建立底层连接/池。幂等。"""

    def close(self) -> None:
        """释放连接资源，子类可覆盖。"""

    @abstractmethod
    def ping(self) -> bool:
        """连通性探测。"""

    def health_check(self) -> dict[str, Any]:
        return {"dialect": self.dialect, "ok": self.ping(),
                "host": self.config.host, "database": self.config.database}

    # ---- 通用探查契约（供上层/MCP 跨方言统一调用）----
    @abstractmethod
    def list_sources(self) -> Result:
        """列出可访问的数据源：关系=表 / Mongo=集合 / Redis=key 概览。"""

    @abstractmethod
    def describe_source(self, name: str) -> Result:
        """描述某个数据源的结构。"""

    @abstractmethod
    def get_source(self, name: str, limit: int = 20) -> Result:
        """取某数据源若干样本。"""

    # ---- 上下文管理 ----
    def __enter__(self):
        self.ensure_ready()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"<{self.__class__.__name__} dialect={self.dialect!r} "
                f"host={self.config.host!r} db={self.config.database!r}>")


# ======================================================================
# 第二层 A：关系型族（SQL + DB-API 2.0 + 连接池）
# ======================================================================
class RelationalConnector(BaseConnector):
    """关系型连接器基类。子类只需提供 dbapi / _connect_kwargs / (_creator_name)。

    保留原有 SQL 能力：query/execute/execute_many/事务/健康检查。
    并把 list_sources/describe_source/get_source 用 information_schema / 方言元数据落地。
    """

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

    # 关系型族：用一条极简 SELECT 探活（不同方言可覆盖，如 Oracle 用 SELECT 1 FROM dual）
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

    # ---- 通用探查契约的 SQL 落地（可被具体方言子类覆盖）----
    def list_sources(self) -> Result:
        # 默认 MySQL/InnoDB 风格元数据；不同方言子类可覆盖
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


# 兼容旧公共名：DBConnector 现在等价于关系型族基类
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
