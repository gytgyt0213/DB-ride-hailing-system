# -*- coding: utf-8 -*-
"""把演示数据的时间戳整体平移到「刚刚」，不重新生成数据。

用途：演示 / 答辩前跑一下，让进行中的订单看起来是刚刚发生的，
而不是「已行程中 175 小时」。数据本身（订单、司机、评价）完全不变。

运行：
    F:\\pythonnewversion\\py39ver\\python.exe scripts\\refresh_demo_time.py
    F:\\pythonnewversion\\py39ver\\python.exe scripts\\refresh_demo_time.py --dry-run

【原理】
    1. 找一个「锚点」——最近一笔进行中订单的下单时间。
       刚生成完数据时，这个锚点约等于「当前时间减几分钟」。
    2. 目标值 = 当前时间 - 5 分钟（让最新的订单看起来是 5 分钟前下的，比较自然）
    3. 偏移量 = 目标值 - 锚点
    4. 把下面 6 张「事件表」里的所有 DATETIME 列统一加上这个偏移量

【与重新生成数据的区别】
    重新生成（gen_fake_data.py --reset）：~15 秒，数据全部重写，时间戳刷新
    本脚本：                              <1 秒，只改时间列，数据一行不动

    两者结果等价（因为生成器是确定性的），但本脚本快得多，
    而且不会动任何业务数据。

【为什么只平移 DATETIME 列，不动 DATE 列】
    DATE 列存的是「绝对日期」（如驾驶证到期日、保险到期日），
    这些是事实，不该随着演示时间平移。只有 DATETIME 才是「事件发生时刻」。
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.connection import get_db, transaction  # noqa: E402

# 需要平移时间戳的表。
# 都是「记录某件事在何时发生」的事件表；
# 不含 pricing_rule（配置有效期，是绝对时间）、user_session（会话应自然过期）、
# 也不含各档案表的注册时间（账号存在久一点是正常的）。
EVENT_TABLES = [
    "ride_order",
    "ride_order_status_log",
    "order_dispatch",
    "order_track",
    "order_review",
    "vehicle_location_current",
]

# 平移后最新的订单应是「几分钟前」下的，看起来才自然
TARGET_LAG_MINUTES = 5


def find_anchor(conn):
    """返回锚点时间：最近一笔进行中订单的下单时间。

    若没有进行中的订单，则退回到「全部订单中最新的下单时间」。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(`created_at`) AS `anchor` FROM `ride_order`"
            " WHERE `status` IN ('accepted','arrived','in_progress')"
        )
        row = cur.fetchone()
        anchor = row["anchor"] if row else None

        if anchor is None:
            cur.execute("SELECT MAX(`created_at`) AS `anchor` FROM `ride_order`")
            row = cur.fetchone()
            anchor = row["anchor"] if row else None

    return anchor


def datetime_columns(conn, table: str):
    """取某张表里所有的 DATETIME 列名。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `COLUMN_NAME` FROM `information_schema`.`COLUMNS`"
            " WHERE `TABLE_SCHEMA` = DATABASE()"
            "   AND `TABLE_NAME` = %s"
            "   AND `DATA_TYPE` = 'datetime'"
            " ORDER BY `ORDINAL_POSITION`",
            (table,),
        )
        return [r["COLUMN_NAME"] for r in cur.fetchall()]


def count_rows(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS `n` FROM `%s`" % table)
        return int(cur.fetchone()["n"])


def main() -> int:
    parser = argparse.ArgumentParser(description="把演示数据的时间戳平移到当前")
    parser.add_argument("--dry-run", action="store_true",
                        help="只计算偏移量并预览，不实际修改数据")
    args = parser.parse_args()

    print("=" * 68)
    print("演示数据时间戳刷新")
    print("=" * 68)

    with get_db() as conn:
        anchor = find_anchor(conn)

        if anchor is None:
            print("\n  数据库里没有任何订单。")
            print("  请先执行数据初始化，或运行 scripts/gen_fake_data.py")
            return 1

        # 用数据库的时钟算偏移量，避免应用与数据库时钟不一致
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TIMESTAMPDIFF(SECOND, %s,"
                "       DATE_SUB(NOW(), INTERVAL %s MINUTE)) AS `delta`",
                (anchor, TARGET_LAG_MINUTES),
            )
            delta = int(cur.fetchone()["delta"])

        print("\n  锚点（最近一笔进行中订单的下单时间）：%s" % anchor)
        print("  偏移量：%+d 秒（约 %.1f 天）" % (delta, delta / 86400.0))

        if delta <= 60:
            print("\n  数据已经很新（偏差不超过 1 分钟），无需刷新。")
            return 0

        # 预览各表将受影响的行数
        print("\n  将平移以下表的所有 DATETIME 列：")
        plan = []
        for table in EVENT_TABLES:
            cols = datetime_columns(conn, table)
            if not cols:
                continue
            n = count_rows(conn, table)
            plan.append((table, cols, n))
            print("    %-26s %2d 列 / %6d 行   (%s)"
                  % (table, len(cols), n, ", ".join(cols)))

        if args.dry_run:
            print("\n  --dry-run：仅预览，未修改任何数据。")
            return 0

        # ---- 在一个事务里完成全部平移 ----
        print("\n  正在平移...")
        with transaction(conn) as cur:
            for table, cols, _ in plan:
                assignments = ", ".join(
                    "`%s` = DATE_ADD(`%s`, INTERVAL %%s SECOND)" % (c, c)
                    for c in cols
                )
                cur.execute(
                    "UPDATE `%s` SET %s" % (table, assignments),
                    tuple([delta] * len(cols)),
                )
                print("    %-26s 更新 %d 行" % (table, cur.rowcount))

        # ---- 复核 ----
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `order_id`, `status`,"
                "       TIMESTAMPDIFF(MINUTE, `created_at`, NOW()) AS `age_min`"
                "  FROM `ride_order`"
                " WHERE `status` IN ('accepted','arrived','in_progress')"
                " ORDER BY FIELD(`status`,'in_progress','arrived','accepted')"
            )
            rows = cur.fetchall()

    print("\n" + "=" * 68)
    print("刷新完成。进行中订单现在的时间：")
    print("=" * 68)
    status_cn = {"accepted": "已接单", "arrived": "司机已到达", "in_progress": "行程中"}
    for r in rows:
        print("  订单 #%-5s %-10s 下单于 %s 分钟前"
              % (r["order_id"], status_cn.get(r["status"], r["status"]), r["age_min"]))

    print("\n  提示：数据内容未变，只是时间戳整体前移了。")
    print("        重新打开浏览器即可看到效果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
