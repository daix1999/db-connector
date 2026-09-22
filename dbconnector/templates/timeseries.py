"""时序数据模型模板（InfluxDB / QuestDB / TimescaleDB / VictoriaMetrics / TDengine）。

面向 agent 的统一动作：按 measurement 写点 + 带时间窗/降采样的范围查询。
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..base import BaseConnector


class TimeSeriesConnector(BaseConnector):
    data_model = "timeseries"

    @abstractmethod
    def list_measurements(self) -> list[str]: ...

    @abstractmethod
    def write(self, measurement: str, fields: dict, tags: dict | None = None,
              timestamp: Any = None) -> Any: ...

    @abstractmethod
    def range_query(self, measurement: str, start: Any, end: Any, *,
                    agg: str | None = None, window: str | None = None,
                    tags: dict | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """agg/window 支持降采样（如 count/mean + 5m），避免把原始高基数点灌给 agent。"""

    def list_sources(self):
        from ..result import Result
        return Result.values(self.list_measurements())

    def describe_source(self, name: str):
        raise NotImplementedError(f"{self.dialect} 需覆盖 describe_source 以返回 tag/field key")

    def get_source(self, name: str, limit: int = 20):
        from datetime import datetime, timedelta, timezone
        end = datetime.now(timezone.utc)
        from ..result import Result
        return Result.docs(self.range_query(name, end - timedelta(hours=1), end, limit=limit))
