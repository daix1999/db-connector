"""内置连接器包：在此 import 各实现以触发 @register。

导入做了容错：即使某方言的可选依赖未装（redis/pymongo），也只跳过它，不影响其余方言注册。
"""
from __future__ import annotations

from . import mysql  # noqa: F401  注册 "mysql"（依赖 PyMySQL）

for _mod in ("redis", "mongodb"):
    try:
        __import__(f"{__name__}.{_mod}")
    except Exception:  # pragma: no cover  可选依赖缺失时静默跳过
        pass
