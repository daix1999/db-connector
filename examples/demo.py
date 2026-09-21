"""最小使用示例：展示连接器的典型用法（需可用 MySQL 才能实际运行）。"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dbconnector import connect, available_dialects  # noqa: E402

print("已注册方言:", available_dialects())

# 凭据从环境变量读取，避免硬编码密码
cfg = dict(
    host=os.getenv("DB_HOST", "127.0.0.1"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_DATABASE", "test"),
)

with connect("mysql", **cfg) as db:
    db.execute(
        "CREATE TABLE IF NOT EXISTS demo_users ("
        " id INT PRIMARY KEY AUTO_INCREMENT, name VARCHAR(50), email VARCHAR(100))"
    )

    new_id = db.execute_returning_id(
        "INSERT INTO demo_users(name, email) VALUES (%s, %s)", ("头儿", "boss@example.com")
    )
    print("新插入用户 id =", new_id)

    users = db.query("SELECT * FROM demo_users WHERE id = %s", (new_id,)).to_list()
    print("查询结果:", users)

    # 事务：要么都成功，要么都回滚
    with db.transaction() as tx:
        tx.execute("UPDATE demo_users SET email=%s WHERE id=%s", ("new@example.com", new_id))
        tx.execute("DELETE FROM demo_users WHERE id=%s", (new_id,))
    print("事务提交后剩余行数 =", db.fetch_value("SELECT COUNT(*) FROM demo_users"))
