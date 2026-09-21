"""端到端冒烟测试 / test 库引导脚本。

会：1) 以管理员账号连接（不带库）→ CREATE DATABASE IF NOT EXISTS
    2) 连接目标库，建表、增删改查、批量、事务（含回滚）逐项验证
    3) 打印每步结果，全部通过退出码 0，任一失败非 0

凭据来源（优先级）：命令行参数 > 环境变量(DB_*) > 内置默认。
用法：
    python scripts/smoke_test.py --user root --password "xxx" --database test
"""
from __future__ import annotations

import argparse
import os
import sys

# Windows 控制台默认 GBK，强制 UTF-8 输出以正常显示中文/emoji/ANSI 颜色
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dbconnector import connect, ConnectorError  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="dbconnector MySQL 冒烟测试")
    p.add_argument("--host", default=os.getenv("DB_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    p.add_argument("--user", default=os.getenv("DB_USER", "root"))
    p.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    p.add_argument("--database", default=os.getenv("DB_DATABASE", "test"))
    p.add_argument("--keep", action="store_true", help="测试后保留表与数据")
    return p.parse_args()


def ok(msg: str) -> None:
    print(f"  \033[32m[PASS]\033[0m {msg}")


def step(msg: str) -> None:
    print(f"\n\033[36m== {msg} ==\033[0m")


def main() -> int:
    a = parse_args()

    # 1) 建库（不带 database）
    step(f"确保数据库 {a.database!r} 存在")
    admin = connect("mysql", host=a.host, port=a.port, user=a.user, password=a.password)
    admin.execute(f"CREATE DATABASE IF NOT EXISTS `{a.database}` DEFAULT CHARSET utf8mb4")
    ok("CREATE DATABASE IF NOT EXISTS 完成")
    admin.close()

    # 2) 连接目标库
    step(f"连接并初始化表 ({a.host}:{a.port}/{a.database})")
    db = connect("mysql", host=a.host, port=a.port, user=a.user,
                 password=a.password, database=a.database, maxconnections=4)

    if not db.ping():
        print("  \033[31m[FAIL]\033[0m ping 失败，无法连接目标库")
        return 1
    ok(f"健康检查通过: {db.health_check()}")

    db.execute("DROP TABLE IF EXISTS _dbc_demo")
    db.execute(
        """CREATE TABLE _dbc_demo (
             id INT PRIMARY KEY AUTO_INCREMENT,
             name VARCHAR(64) NOT NULL,
             score INT NOT NULL DEFAULT 0
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
    )
    ok("建表 _dbc_demo 完成")

    # 3) 单条插入 + lastrowid
    step("INSERT + lastrowid")
    rid = db.execute_returning_id("INSERT INTO _dbc_demo(name, score) VALUES (%s, %s)", ("alice", 90))
    assert rid and rid > 0, "lastrowid 应该 > 0"
    ok(f"插入成功，自增主键 id={rid}")

    # 4) 批量插入
    step("executemany 批量插入")
    n = db.execute_many(
        "INSERT INTO _dbc_demo(name, score) VALUES (%s, %s)",
        [("bob", 75), ("carol", 88), ("dave", 60)],
    )
    ok(f"批量插入受影响行数={n}（期望 3 或 -1，取决于驱动）")

    # 5) 查询
    step("SELECT / fetch_value")
    total = db.fetch_value("SELECT COUNT(*) FROM _dbc_demo")
    ok(f"当前行数={total}")
    res = db.query("SELECT id, name, score FROM _dbc_demo WHERE score >= %s ORDER BY score DESC", (70,))
    assert res.columns == ["id", "name", "score"], f"列名不符: {res.columns}"
    for row in res.to_list():
        print(f"      row -> {row}")
    ok(f"结果集解析正常（{len(res)} 行，列={res.columns}）")

    # 6) 事务回滚
    step("事务回滚验证")
    try:
        with db.transaction() as tx:
            tx.execute("INSERT INTO _dbc_demo(name, score) VALUES (%s, %s)", ("rollback_me", 100))
            raise RuntimeError("主动抛错以触发回滚")
    except RuntimeError:
        pass
    exists = db.fetch_value("SELECT COUNT(*) FROM _dbc_demo WHERE name='rollback_me'")
    assert exists == 0, "回滚失败！记录仍存在"
    ok("异常触发回滚，脏数据未落库")

    # 7) 事务提交
    step("事务提交验证")
    with db.transaction() as tx:
        tx.execute("INSERT INTO _dbc_demo(name, score) VALUES (%s, %s)", ("commit_me", 100))
        tx.execute("UPDATE _dbc_demo SET score = score + 1 WHERE name = %s", ("alice",))
    committed = db.fetch_value("SELECT COUNT(*) FROM _dbc_demo WHERE name='commit_me'")
    alice_score = db.fetch_value("SELECT score FROM _dbc_demo WHERE name='alice'")
    assert committed == 1, "提交失败"
    assert alice_score == 91, f"事务内 UPDATE 未生效，alice score={alice_score}"
    ok("事务正常提交，两条写操作都生效")

    # 8) 清理
    if not a.keep:
        step("清理测试表")
        db.execute("DROP TABLE IF EXISTS _dbc_demo")
        ok("已 DROP _dbc_demo")
    else:
        print("  (保留测试表，未清理)")

    db.close()
    print("\n\033[32m全部冒烟测试通过 ✅\033[0m")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConnectorError as e:
        print(f"\n\033[31m连接器错误:\033[0m {e}")
        sys.exit(2)
