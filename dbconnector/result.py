"""统一结果封装。

- Result：跨方言的通用形状，kind 标明数据形态
  - rows      关系型行（list[dict]）+ columns
  - documents 文档（list[dict]，Mongo）
  - keyvalue  键值（dict）
  - values    扁平值列表（Redis list/set 成员）
  - scalar    单值
- ResultSet：历史 API（关系型专用）保留，向后兼容，Result 的 rows 视图。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class Result:
    kind: str = "rows"                       # rows|documents|keyvalue|values|scalar
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    scalar: Any = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        if self.kind == "keyvalue":
            return len(self.rows[0]) if self.rows else 0
        if self.kind == "scalar":
            return 1
        return len(self.rows)

    def __iter__(self) -> Iterator[Any]:
        if self.kind == "keyvalue" and self.rows:
            return iter(self.rows[0].items())
        if self.kind == "scalar":
            return iter([self.scalar])
        return iter(self.rows)

    @property
    def to_list(self) -> list[Any]:
        return self.rows if self.kind != "scalar" else [self.scalar]

    # ---- 便捷构造 ----
    @classmethod
    def from_rows(cls, columns: list[str], rows: list[dict[str, Any]], **meta) -> "Result":
        return cls(kind="rows", columns=columns, rows=rows, meta=meta)

    @classmethod
    def docs(cls, docs: list[dict[str, Any]], **meta) -> "Result":
        return cls(kind="documents", rows=docs, meta=meta)

    @classmethod
    def kv(cls, mapping: dict[str, Any], **meta) -> "Result":
        return cls(kind="keyvalue", rows=[dict(mapping)], meta=meta)

    @classmethod
    def values(cls, seq: list[Any], **meta) -> "Result":
        return cls(kind="values", rows=[{"value": v} for v in seq], meta=meta)

    @classmethod
    def single(cls, value: Any, **meta) -> "Result":
        return cls(kind="scalar", scalar=value, meta=meta)

    def dict(self) -> dict[str, Any]:
        """通用序列化，供 MCP/JSON 返回。"""
        return {"kind": self.kind, "columns": self.columns, "rows": self.rows,
                "scalar": self.scalar, "meta": self.meta}


@dataclass
class ResultSet:
    """兼容旧接口的关系型结果（等价于 Result(kind='rows') 的视图）。"""

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    rowcount: int = 0
    lastrowid: int | None = None

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.rows)

    @property
    def scalar(self) -> Any:
        if self.rows and self.columns:
            return self.rows[0][self.columns[0]]
        return None

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def column(self, name: str) -> list[Any]:
        return [r[name] for r in self.rows]

    def to_list(self) -> list[dict[str, Any]]:
        return list(self.rows)

    def as_result(self) -> Result:
        return Result.from_rows(self.columns, self.rows, rowcount=self.rowcount,
                                lastrowid=self.lastrowid)
