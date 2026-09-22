"""第一层：BaseConnector —— 所有数据模型共同满足的最小契约。

这里是"为 agent 设计"的关键：跨方言统一的探查方法（list_sources / describe_source /
get_source）让 agent 用同一套动作即可摸清任意数据库，而不必为每种库学习不同 API。

连接器本身能力全开（读写都提供），不含只读判断；权限裁剪交给使用层。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from . import audit
from .config import ConnectorConfig
from .result import Result


class BaseConnector(ABC):
    """与数据模型无关的最小公共接口。"""

    #: 方言名，由 @register 注入
    dialect: str = ""
    #: 数据模型族：relational | keyvalue | document | columnar | graph | search | ...
    data_model: str = ""
    #: 该方言默认端口，子类覆盖
    default_port: int | None = None
    #: 需要自动审计的操作方法名（各模板声明；BaseConnector 在建类时统一包装）
    AUDITED_OPS: tuple = ()

    def __init_subclass__(cls, **kw):
        """按 AUDITED_OPS 自动包装操作 → 无论经 MCP 还是直调库，落库操作都留痕。
        已包装的方法跳过，避免叶子类重复包装。"""
        super().__init_subclass__(**kw)
        for name in getattr(cls, "AUDITED_OPS", ()):
            fn = getattr(cls, name, None)
            if fn is None or getattr(fn, "__isabstractmethod__", False):
                continue  # 抽象桩不包，等具体实现所在类再包
            if callable(fn) and not getattr(fn, "_audited", False):
                setattr(cls, name, audit.wrap_operation(fn, name))

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
        """释放连接资源，子类覆盖。"""

    @abstractmethod
    def ping(self) -> bool:
        """连通性探测。"""

    def health_check(self) -> dict[str, Any]:
        return {"dialect": self.dialect, "data_model": self.data_model, "ok": self.ping(),
                "host": self.config.host, "database": self.config.database}

    # ---- 跨方言统一探查契约（agent 学一次、处处可用）----
    @abstractmethod
    def list_sources(self) -> Result:
        """列出可访问的数据单元：关系=表 / 文档=集合 / 键值=key 概览。"""

    @abstractmethod
    def describe_source(self, name: str) -> Result:
        """描述某个数据单元的结构。"""

    @abstractmethod
    def get_source(self, name: str, limit: int = 20) -> Result:
        """取某数据单元若干样本。"""

    # ---- 上下文管理 ----
    def __enter__(self):
        self.ensure_ready()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"<{self.__class__.__name__} dialect={self.dialect!r} "
                f"host={self.config.host!r} db={self.config.database!r}>")
