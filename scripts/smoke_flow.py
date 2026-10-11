# -*- coding: utf-8 -*-
"""端到端业务流程测试：通过 Web 界面走完「下单→派单→接单→到达→行程→结算→评价」。

运行（需先启动 uvicorn）：
    F:\\pythonnewversion\\py39ver\\python.exe scripts\\smoke_flow.py

这个测试不绕过任何一层：全部通过真实的 HTTP 表单提交，
因此同时验证了路由、服务层、存储过程和数据库约束。
"""
from __future__ import annotations

import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, __file__.rsplit("\\", 1)[0] + "\\..")

from db.connection import get_db  # noqa: E402

BASE = "http://127.0.0.1:8000"
PASSWORD = "123456"


class Session(object):
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path):
        try:
            with self.opener.open(BASE + path, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.geturl()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), path

    def post(self, path, data):
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=body, method="POST")
        try:
            with self.opener.open(req, timeout=25) as r:
                return r.status, r.read().decode("utf-8", "replace"), r.geturl()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), path


def step(msg):
    print("\n" + "-" * 70)
    print("  " + msg)
    print("-" * 70)


def ok(cond, msg):
    print("  %s %s" % ("[OK]  " if cond else "[FAIL]", msg))
    return cond


def main():
    failures = []

    # ---------- 准备：找一个有在线可接单司机的区域 ----------
    step("准备：寻找一个存在可接单司机的区域")
    with get_db() as conn:
        with conn.cursor() as cur:
            # 找一个确实有「空闲可接单司机」的区域。
            # 直接查 v_available_drivers —— 它已经包含「在线 + 已审核 +
            # 有可运营车辆 + 当前没有进行中订单」的全部条件。
            #
            # ⚠️ 必须在 Python 侧排除 concurrency_test.py 留下的 test_race_* 区域：
            #    那些司机用的密码是 test1234，本脚本用 123456 登录会失败。
            #    两个脚本谁先跑不该影响结果。
            #    这里刻意不用 SQL 的 NOT LIKE —— 在 MySQL 字符串字面量里
            #    `\_` 的反斜杠会先被字符串解析器吃掉，导致下划线退化成单字符
            #    通配符而过度匹配。用 Python 过滤语义明确、无歧义。
            cur.execute(
                "SELECT d.`region`, COUNT(*) AS n"
                "  FROM `v_available_drivers` d"
                "  JOIN `vehicle` v ON v.`driver_id` = d.`driver_id`"
                "                  AND v.`operate_status` = 1 AND v.`car_type_id` = 1"
                " GROUP BY d.`region` ORDER BY n DESC"
            )
            candidates = [r for r in cur.fetchall()
                          if not r["region"].startswith("test_race")]
            row = candidates[0] if candidates else None

    if not row:
        print("  [FAIL] 数据库中没有可接单的在线司机，无法测试完整流程")
        print("         请先执行 sql/06_seed_demo.sql 导入演示数据")
        return 1

    region = row["region"]
    print("  [OK]   选定区域：%s（有 %d 名可接单司机）" % (region, row["n"]))

    # ---------- 1. 乘客登录并下单 ----------
    step("步骤 1：乘客登录并下单")
    p = Session()
    st, _, _ = p.post("/auth/login", {"username": "p001", "password": PASSWORD, "next": ""})
    if not ok(st == 200, "乘客 p001 登录"):
        return 1

    # 先数一下下单前的订单数
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM `ride_order`")
            before = cur.fetchone()["n"]

    st, body, url = p.post("/passenger/orders/new", {
        "car_type_id": 1,
        "origin_address": "端到端测试起点-%s" % region,
        "origin_region": region,
        "dest_address": "端到端测试终点",
        "remark": "自动化流程测试",
        "address_id": "",
    })
    if not ok(st == 200, "提交下单表单"):
        failures.append("下单")
        return 1

    m = re.search(r"/passenger/orders/(\d+)", url) or re.search(r"/passenger/orders/(\d+)", body)
    if not ok(m is not None, "跳转到订单详情页"):
        return 1
    order_id = int(m.group(1))
    print("         订单 ID = %s" % order_id)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM `ride_order`")
            after = cur.fetchone()["n"]
            cur.execute(
                "SELECT `order_no`,`status`,`snap_base_price`,`snap_per_km_price`,"
                "       `rule_version`,`origin_region`"
                "  FROM `ride_order` WHERE `order_id` = %s", (order_id,))
            order = cur.fetchone()
            # 只挑「当前没有进行中订单」的被邀司机，避免撞上 uk_active_driver
            cur.execute(
                "SELECT od.`driver_id`, d.`account_id`, a.`username`, d.`region`"
                "  FROM `order_dispatch` od"
                "  JOIN `driver`  d ON d.`driver_id`  = od.`driver_id`"
                "  JOIN `account` a ON a.`account_id` = d.`account_id`"
                " WHERE od.`order_id` = %s AND od.`status` = 'invited'"
                "   AND NOT EXISTS (SELECT 1 FROM `ride_order` o"
                "                    WHERE o.`driver_id` = od.`driver_id`"
                "                      AND o.`status` IN ('accepted','arrived','in_progress'))"
                " ORDER BY od.`dispatch_id` LIMIT 1", (order_id,))
            invite = cur.fetchone()

    ok(after == before + 1, "数据库新增 1 条订单（%d -> %d）" % (before, after))
    ok(order["status"] == "pending", "订单状态为「待接单」")
    ok(order["snap_base_price"] is not None, "计价规则快照已写入（起步价 %s）" % order["snap_base_price"])
    ok(order["origin_region"] == region, "订单区域与司机区域一致（%s）" % region)
    if not invite:
        print("  [FAIL] 没有找到派单邀请，无法继续")
        return 1
    ok(True, "已派单给司机 #%s（%s，区域 %s）" % (invite["driver_id"], invite["username"], invite["region"]))

    # ---------- 2. 司机登录并接单 ----------
    step("步骤 2：司机登录并接单")
    d = Session()
    st, _, _ = d.post("/auth/login", {"username": invite["username"],
                                      "password": PASSWORD, "next": ""})
    if not ok(st == 200, "司机 %s 登录" % invite["username"]):
        return 1

    st, body, _ = d.post("/driver/orders/%d/accept" % order_id, {})
    if not ok("接单成功" in body, "司机提交接单（页面提示接单成功）"):
        m = re.search(r'class="flash flash-\w+">([^<]+)<', body)
        print("         页面提示：%s" % (m.group(1).strip() if m else "（未捕获到提示）"))

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT `status`,`driver_id`,`vehicle_id` FROM `ride_order`"
                        " WHERE `order_id` = %s", (order_id,))
            o = cur.fetchone()
            cur.execute("SELECT `status` FROM `order_dispatch`"
                        " WHERE `order_id` = %s AND `status` = 'accepted'", (order_id,))
            acc = cur.fetchall()

    ok(o["status"] == "accepted", "订单状态变为「已接单」")
    ok(o["driver_id"] == invite["driver_id"], "订单归属正确的司机")
    ok(o["vehicle_id"] is not None, "已绑定执行车辆 #%s" % o["vehicle_id"])
    ok(len(acc) == 1, "恰好 1 条邀请被标记为 accepted")

    # ---------- 3. 到达 → 开始行程 ----------
    step("步骤 3：司机到达上车点 → 开始行程")
    d.post("/driver/orders/%d/arrive" % order_id, {})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT `status`,`arrived_at` FROM `ride_order`"
                        " WHERE `order_id` = %s", (order_id,))
            o = cur.fetchone()
    ok(o["status"] == "arrived" and o["arrived_at"] is not None, "状态变为「司机已到达」且有到达时间")

    d.post("/driver/orders/%d/start" % order_id, {})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT `status`,`started_at` FROM `ride_order`"
                        " WHERE `order_id` = %s", (order_id,))
            o = cur.fetchone()
    ok(o["status"] == "in_progress" and o["started_at"] is not None, "状态变为「行程中」且有开始时间")

    # ---------- 4. 结束行程并结算 ----------
    step("步骤 4：结束行程并结算")
    distance, duration = 12.5, 28.0
    st, body, _ = d.post("/driver/orders/%d/complete" % order_id,
                         {"distance_km": distance, "duration_min": duration})

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `status`,`fare_amount`,`distance_km`,`duration_min`,"
                "       `snap_base_price`,`snap_base_distance_km`,"
                "       `snap_per_km_price`,`snap_per_min_price`,`snap_min_price`"
                "  FROM `ride_order` WHERE `order_id` = %s", (order_id,))
            o = cur.fetchone()
            cur.execute("SELECT `fn_calc_fare`(%s,%s,%s,%s,%s,%s,%s) AS f",
                        (o["snap_base_price"], o["snap_base_distance_km"],
                         o["snap_per_km_price"], o["snap_per_min_price"],
                         o["snap_min_price"], distance, duration))
            expect = cur.fetchone()["f"]

    ok(o["status"] == "completed", "订单状态变为「已完成」")
    ok(o["fare_amount"] is not None, "已生成车费：￥%s" % o["fare_amount"])

    if o["fare_amount"] is None:
        print("  [SKIP] 车费为空，跳过计价核对（说明前面的步骤没有真正走通）")
        return 1

    ok(float(o["fare_amount"]) == float(expect),
       "车费与 fn_calc_fare 一致（期望 %s）" % expect)
    manual = (float(o["snap_base_price"])
              + max(0.0, distance - float(o["snap_base_distance_km"])) * float(o["snap_per_km_price"])
              + duration * float(o["snap_per_min_price"]))
    manual = max(manual, float(o["snap_min_price"]))
    ok(abs(float(o["fare_amount"]) - round(manual, 2)) < 0.01,
       "车费与手工公式一致（手工 %s）" % round(manual, 2))

    # ---------- 5. 状态日志完整性 ----------
    step("步骤 5：校验状态流转日志")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `to_status` FROM `ride_order_status_log`"
                " WHERE `order_id` = %s ORDER BY `log_id`", (order_id,))
            logs = [r["to_status"] for r in cur.fetchall()]

    expected = ["pending", "accepted", "arrived", "in_progress", "completed"]
    ok(logs == expected, "状态日志完整且顺序正确：%s" % " → ".join(logs))

    # ---------- 6. 乘客评价 ----------
    step("步骤 6：乘客提交评价")
    st, body, _ = p.post("/passenger/orders/%d/review" % order_id,
                         {"score": 5, "content": "端到端测试评价：司机很准时",
                          "tags": ["准时", "态度好"]})

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT `score`,`content` FROM `order_review`"
                        " WHERE `order_id` = %s", (order_id,))
            review = cur.fetchone()
            cur.execute("SELECT `service_score`,`review_count` FROM `driver`"
                        " WHERE `driver_id` = %s", (invite["driver_id"],))
            drv = cur.fetchone()
            cur.execute(
                "SELECT `stored_score`,`actual_avg_score`,`stored_count`,`actual_count`"
                "  FROM `v_driver_service_score` WHERE `driver_id` = %s",
                (invite["driver_id"],))
            chk = cur.fetchone()

    ok(review is not None and review["score"] == 5, "评价已写入")
    ok(drv["review_count"] > 0, "触发器已更新司机评价数（%s）" % drv["review_count"])
    ok(chk["stored_count"] == chk["actual_count"],
       "触发器维护值与实时聚合值一致（%s vs %s）" % (chk["stored_count"], chk["actual_count"]))

    # ---------- 7. 重复评价应被拒绝 ----------
    step("步骤 7：重复评价应被拒绝")
    st, body, _ = p.post("/passenger/orders/%d/review" % order_id,
                         {"score": 4, "content": "重复评价"})
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM `order_review`"
                        " WHERE `order_id` = %s", (order_id,))
            n = cur.fetchone()["n"]
    ok(n == 1, "同一订单仍只有 1 条评价（唯一键 uk_review_order 生效）")

    # ---------- 8. 并发抢单（数据库层） ----------
    step("步骤 8：一司多单防护（uk_active_driver）")
    print("         说明：由 scripts/concurrency_test.py 做完整并发测试，此处仅验证索引存在")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.STATISTICS"
                " WHERE TABLE_SCHEMA = 'taxi_system' AND TABLE_NAME = 'ride_order'"
                "   AND INDEX_NAME = 'uk_active_driver'")
            n = cur.fetchone()["n"]
    ok(n > 0, "生成列唯一索引 uk_active_driver 存在于数据库")

    # ---------- 汇总 ----------
    print("\n" + "=" * 70)
    print("端到端流程测试完成：订单 #%s 走完 下单 → 派单 → 接单 → 到达 → 行程 → 结算 → 评价" % order_id)
    print("最终车费：￥%s（%s km / %s 分钟）" % (o["fare_amount"], distance, duration))
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
