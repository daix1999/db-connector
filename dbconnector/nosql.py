"""已弃用：NoSQL 族模板已迁移到 dbconnector.templates。

保留此模块仅为向后兼容旧 import（from dbconnector.nosql import ...）。
"""
from __future__ import annotations

from .templates.document import DocumentConnector
from .templates.keyvalue import KeyValueConnector

__all__ = ["DocumentConnector", "KeyValueConnector"]
