"""列式 / OLAP 数据模型模板（ClickHouse / Apache Doris / StarRocks / Druid / Greenplum）。

关系型模板的专化：仍是 SQL，但 OLAP 语义下"一次扫描上亿行"是常态，
因此面向 agent 增加了扫描护栏与分区/基数探查，避免误查把资源打满或把巨量结果灌进上下文。
"""
from __future__ import annotations

from typing import Any

from .relational import RelationalConnector


class ColumnarConnector(RelationalConnector):
    """OLAP 列式族模板。继承全部 SQL 能力，叠加分析型安全默认。"""

    data_model = "columnar"
    #: OLAP 单次返回默认更保守（agent 省 token + 防大结果）
    default_limit = 100
    max_limit = 5000

    def get_source(self, name: str, limit: int | None = None):
        """样本读取默认收紧到 default_limit。"""
        lim = int(limit or self.default_limit)
        return super().get_source(name, min(lim, self.max_limit))

    def list_partitions(self, name: str) -> Any:
        """分区探查（分区裁剪前先看结构）。子类按方言实现，如 ClickHouse system.parts。"""
        raise NotImplementedError(f"{self.dialect} 未实现 list_partitions")

    def estimate_scan(self, sql: str) -> dict[str, Any]:
        """执行前的代价/行数估算，供 agent 判断是否需要加 WHERE/分区裁剪。
        子类可映射到 EXPLAIN / system.query 估算。默认返回未知。"""
        return {"dialect": self.dialect, "estimated_rows": None, "note": "未实现代价估算"}
