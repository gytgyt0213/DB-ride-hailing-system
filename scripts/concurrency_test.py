# -*- coding: utf-8 -*-
"""并发接单测试 —— 验证「两个司机同时抢同一订单」只会有一人成功。

运行：
    F:\\pythonnewversion\\py39ver\\python.exe scripts\\concurrency_test.py

【为什么需要这个脚本】
    顺序调用 sp_accept_order 是**验证不到并发问题的**：
    第一个司机接单成功后，过程内部已经把其他司机的邀请置为 cancelled，
    第二个司机再调用时会在「邀请有效性」前置检查就被拦下，
    根本走不到那句关键的 `UPDATE ... WHERE status='pending'`。
    必须让 N 个线程用**各自独立的连接**在同一瞬间冲进去，
    才能真正压到 ROW_COUNT() 这条并发守卫上。

【本脚本验证三件事】
    TEST 1  并发抢占   —— N 个司机同时接同一单，有且仅有 1 人成功
    TEST 2  状态一致性 —— 订单最终只属于 1 人，状态日志只多出 1 条 accepted
    TEST 3  数据库兜底 —— 生成列唯一索引 uk_active_driver 阻止一司多单
                          （即使绕过应用层逻辑直接改库，也插不进第二条）
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Windows 控制台默认不是 UTF-8，中文会输出成乱码，这里显式纠正
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 让脚本能 import 到项目根的 config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402
from pymysql.cursors import DictCursor  # noqa: E402

from config import DBConfig  # noqa: E402
from security.password import hash_password  # noqa: E402


# 并发司机数量
DRIVER_COUNT = 5
# 测试区域前缀。
# ⚠️ 实际使用的区域会拼接本次运行的时间戳后缀，保证「每轮测试独占一个区域」。
#    否则历次运行残留的同区域在线司机会被 sp_dispatch_order 优先选中
#    （它按 service_score DESC, driver_id ASC 排序取前 N 名），
#    导致邀请发给上一轮的旧司机，本次的新司机全部拿到 NOT_INVITED。
TEST_REGION_PREFIX = "test_race"


def connect() -> pymysql.connections.Connection:
    """每个线程必须用独立连接 —— pymysql 的 Connection 不是线程安全的。"""
    return pymysql.connect(
        host=DBConfig.host,
        port=DBConfig.port,
        user=DBConfig.user,
        password=DBConfig.password,
        database=DBConfig.database,
        charset=DBConfig.charset,
        cursorclass=DictCursor,
        autocommit=False,
    )


def call_proc(cur, sql: str, args: Tuple[Any, ...], outs: List[str]) -> Dict[str, Any]:
    """调用带 OUT 参数的存储过程并取回 OUT 值。

    pymysql 没有原生 OUT 参数支持，标准做法是：
        CALL proc(%s, %s, @o1, @o2)   然后   SELECT @o1, @o2
    用户变量是连接级的，所以同一个连接里取回是安全的。
    """
    placeholders = ", ".join(["%s"] * len(args))
    out_vars = ", ".join("@" + name for name in outs)
    cur.execute("CALL %s(%s%s)" % (sql, placeholders, (", " + out_vars) if outs else ""), args)
    # 消费掉存储过程可能产生的额外结果集
    while cur.nextset():
        pass
    if outs:
        cur.execute("SELECT " + ", ".join("@%s AS `%s`" % (n, n) for n in outs))
        return cur.fetchone() or {}
    return {}


def setup_test_data(conn) -> Dict[str, Any]:
    """创建本次测试专属的乘客、司机、车辆，并下一单、派单。

    所有用户名带时间戳后缀，保证脚本可反复运行。
    """
    suffix = str(int(time.time() * 1000))[-9:]
    # 本次运行独占的区域，隔离历次测试残留数据
    region = "%s_%s" % (TEST_REGION_PREFIX, suffix)
    salt, pwd_hash, iterations = hash_password("test1234")

    with conn.cursor() as cur:
        # ---- 乘客 ----
        cur.execute(
            "INSERT INTO `account` (`username`,`password_hash`,`salt`,`pwd_iterations`,`role_code`)"
            " VALUES (%s,%s,%s,%s,'passenger')",
            ("race_p_" + suffix, pwd_hash, salt, iterations),
        )
        passenger_account = cur.lastrowid
        cur.execute(
            "INSERT INTO `passenger` (`account_id`,`real_name`,`phone`,`gender`,`home_region`)"
            " VALUES (%s,%s,%s,1,%s)",
            (passenger_account, "并发测试乘客", "139" + suffix[:8], region),
        )
        passenger_id = cur.lastrowid

        # ---- 司机 + 车辆 ----
        drivers: List[int] = []
        for i in range(DRIVER_COUNT):
            cur.execute(
                "INSERT INTO `account` (`username`,`password_hash`,`salt`,`pwd_iterations`,`role_code`)"
                " VALUES (%s,%s,%s,%s,'driver')",
                ("race_d%d_%s" % (i + 1, suffix), pwd_hash, salt, iterations),
            )
            driver_account = cur.lastrowid
            cur.execute(
                "INSERT INTO `driver`"
                " (`account_id`,`real_name`,`phone`,`region`,`online_status`,`audit_status`)"
                " VALUES (%s,%s,%s,%s,1,1)",
                (driver_account, "并发司机%d" % (i + 1), "138" + suffix[:5] + "%03d" % i, region),
            )
            driver_id = cur.lastrowid
            drivers.append(driver_id)
            cur.execute(
                "INSERT INTO `vehicle`"
                " (`driver_id`,`car_type_id`,`plate_no`,`brand`,`model`,`color`,`seat_count`,`operate_status`)"
                " VALUES (%s,1,%s,'测试','测试','白',4,1)",
                (driver_id, "测T%d%s" % (i, suffix[-6:-2])),
            )

        # ---- 下单（经济型 car_type_id=1）----
        cur.execute(
            "CALL `sp_create_order`(%s,1,%s,%s,%s,%s,%s,%s,%s,%s,@oid,@ono,@code,@msg)",
            (passenger_id, "并发测试起点", 116.48, 39.99, region,
             "并发测试终点", 116.46, 39.91, "并发测试"),
        )
        while cur.nextset():
            pass
        cur.execute("SELECT @oid AS oid, @ono AS ono, @code AS code, @msg AS msg")
        created = cur.fetchone()
        order_id = created["oid"]

        # ---- 派单给全部司机 ----
        cur.execute("CALL `sp_dispatch_order`(%s,%s,@batch,@inv,@code,@msg)",
                    (order_id, DRIVER_COUNT))
        while cur.nextset():
            pass
        cur.execute("SELECT @batch AS batch, @inv AS invited, @code AS code, @msg AS msg")
        dispatched = cur.fetchone()

    conn.commit()

    return {
        "passenger_id": passenger_id,
        "drivers": drivers,
        "region": region,
        "order_id": order_id,
        "order_no": created["ono"],
        "create_code": created["code"],
        "invited": dispatched["invited"],
        "dispatch_code": dispatched["code"],
    }


def test_concurrent_accept(order_id: int, drivers: List[int]) -> List[Dict[str, Any]]:
    """TEST 1+2：N 个线程同一瞬间抢同一订单。"""
    n = len(drivers)
    barrier = threading.Barrier(n)
    results: List[Optional[Dict[str, Any]]] = [None] * n
    errors: List[Optional[str]] = [None] * n

    # 先把连接建好，避免「建连接」的耗时把并发窗口拉开
    conns = [connect() for _ in range(n)]

    def worker(index: int) -> None:
        conn = conns[index]
        try:
            cur = conn.cursor()
            barrier.wait()          # ← 所有线程在此对齐，然后同时冲
            out = call_proc(cur, "`sp_accept_order`",
                            (order_id, drivers[index]), ["vehicle_id", "code", "msg"])
            conn.commit()
            results[index] = {
                "driver_id": drivers[index],
                "code": out.get("code"),
                "msg": out.get("msg"),
                "vehicle_id": out.get("vehicle_id"),
            }
        except Exception as exc:  # noqa: BLE001
            errors[index] = repr(exc)
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    for i, err in enumerate(errors):
        if err:
            results[i] = {"driver_id": drivers[i], "code": "EXCEPTION", "msg": err}
    return [r for r in results if r is not None]


def test_active_driver_backstop(winner_driver_id: int, passenger_id: int, region: str) -> Dict[str, Any]:
    """TEST 3：生成列唯一索引 uk_active_driver 的数据库层兜底。

    【为什么要手工插邀请记录】
      sp_dispatch_order 只会从 v_available_drivers 里挑候选人，
      而该视图已经排除了「正在跑单」的司机 —— 这是正确行为，
      意味着平台根本不会去邀请一个忙单司机。

      所以要通过正常派单路径构造这个场景是不可能的。
      本测试要验证的是**竞态兜底**：司机被邀请时确实空闲，
      但在应答之前先被派了别的单（或并发接了别的单），
      此时数据库层的唯一索引必须把这次接单挡下来。
      因此这里直接写入邀请记录，绕过派单筛选，精确复现该竞态。
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            # 1) 正常下一单
            cur.execute(
                "CALL `sp_create_order`(%s,1,%s,%s,%s,%s,%s,%s,%s,%s,@oid,@ono,@code,@msg)",
                (passenger_id, "兜底测试起点", 116.48, 39.99, region,
                 "兜底测试终点", 116.46, 39.91, "兜底测试"),
            )
            while cur.nextset():
                pass
            cur.execute("SELECT @oid AS oid")
            order_id = cur.fetchone()["oid"]

            # 2) 直接写入邀请，模拟「邀请在先、变忙在后」的竞态
            cur.execute(
                "INSERT INTO `order_dispatch`"
                " (`order_id`,`driver_id`,`status`,`batch_no`,`invited_at`,`expire_at`)"
                " VALUES (%s,%s,'invited',1,NOW(),DATE_ADD(NOW(), INTERVAL 60 SECOND))",
                (order_id, winner_driver_id),
            )

            cur.execute("SELECT COUNT(*) AS n FROM `order_dispatch`"
                        " WHERE `order_id`=%s AND `driver_id`=%s AND `status`='invited'",
                        (order_id, winner_driver_id))
            invited = cur.fetchone()["n"]

            # 3) 该司机此时已有一个进行中订单，再接这一单必须被数据库拦下
            out = call_proc(cur, "`sp_accept_order`",
                            (order_id, winner_driver_id), ["vehicle_id", "code", "msg"])
        conn.commit()
        return {
            "order_id": order_id,
            "invited": invited,
            "code": out.get("code"),
            "msg": out.get("msg"),
        }
    finally:
        conn.close()


def verify_state(order_id: int) -> Dict[str, Any]:
    """回查数据库，确认最终状态一致。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `status`, `driver_id`, `vehicle_id`, `accepted_at`"
                " FROM `ride_order` WHERE `order_id`=%s", (order_id,))
            order = cur.fetchone()

            cur.execute(
                "SELECT COUNT(*) AS n FROM `ride_order_status_log`"
                " WHERE `order_id`=%s AND `to_status`='accepted'", (order_id,))
            accepted_logs = cur.fetchone()["n"]

            cur.execute(
                "SELECT `status`, COUNT(*) AS n FROM `order_dispatch`"
                " WHERE `order_id`=%s GROUP BY `status`", (order_id,))
            dispatch_stats = {r["status"]: r["n"] for r in cur.fetchall()}

        return {
            "order": order,
            "accepted_log_count": accepted_logs,
            "dispatch_stats": dispatch_stats,
        }
    finally:
        conn.close()


def cleanup(ids: Dict[str, Any]) -> None:
    """清理本次测试产生的数据（仅删本次创建的账号，不碰其他数据）。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            cur.execute("DELETE FROM `order_review` WHERE `order_id`=%s", (ids["order_id"],))
            cur.execute("DELETE FROM `order_track` WHERE `order_id`=%s", (ids["order_id"],))
            cur.execute("DELETE FROM `ride_order_status_log` WHERE `order_id`=%s", (ids["order_id"],))
            cur.execute("DELETE FROM `order_dispatch` WHERE `order_id`=%s", (ids["order_id"],))
            cur.execute("DELETE FROM `ride_order` WHERE `order_id`=%s", (ids["order_id"],))
            cur.execute("DELETE FROM `vehicle` WHERE `driver_id` IN (%s)"
                        % ",".join(["%s"] * len(ids["drivers"])), tuple(ids["drivers"]))
            cur.execute("DELETE FROM `driver` WHERE `driver_id` IN (%s)"
                        % ",".join(["%s"] * len(ids["drivers"])), tuple(ids["drivers"]))
            cur.execute("DELETE FROM `passenger` WHERE `passenger_id`=%s", (ids["passenger_id"],))
            cur.execute("DELETE FROM `account` WHERE `username` LIKE 'race\\_%'")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    print("=" * 68)
    print("并发接单测试")
    print("数据库：%s" % DBConfig.describe())
    print("并发司机数：%d" % DRIVER_COUNT)
    print("=" * 68)

    setup_conn = connect()
    try:
        ids = setup_test_data(setup_conn)
    finally:
        setup_conn.close()

    order_id = ids["order_id"]
    print("\n[准备] 订单 #%s (%s)  下单=%s  派单=%s  已邀请 %s 名司机"
          % (order_id, ids["order_no"], ids["create_code"], ids["dispatch_code"], ids["invited"]))

    if ids["invited"] < 2:
        print("\n[中止] 邀请的司机不足 2 名，无法构成并发场景。请检查司机区域/车辆状态。")
        return 2

    # ---------- TEST 1：并发抢占 ----------
    print("\n" + "-" * 68)
    print("TEST 1   %d 个司机同时接单" % len(ids["drivers"]))
    print("-" * 68)

    started = time.time()
    results = test_concurrent_accept(order_id, ids["drivers"])
    elapsed = time.time() - started

    for r in results:
        print("  司机 #%-4s -> %-14s %s" % (r["driver_id"], r["code"], r["msg"]))

    ok_results = [r for r in results if r["code"] == "OK"]
    print("\n  耗时：%.3f 秒" % elapsed)
    print("  成功接单人数：%d（期望 1）" % len(ok_results))

    test1_pass = len(ok_results) == 1

    # ---------- TEST 2：状态一致性 ----------
    print("\n" + "-" * 68)
    print("TEST 2   数据库最终状态一致性")
    print("-" * 68)

    state = verify_state(order_id)
    order = state["order"]
    print("  订单状态：%s" % order["status"])
    print("  最终司机：%s" % order["driver_id"])
    print("  执行车辆：%s" % order["vehicle_id"])
    print("  accepted 状态日志条数：%d（期望 1）" % state["accepted_log_count"])
    print("  派单记录分布：%s" % state["dispatch_stats"])

    winner_ids = {r["driver_id"] for r in ok_results}
    test2_pass = (
        order["status"] == "accepted"
        and order["driver_id"] in winner_ids
        and state["accepted_log_count"] == 1
    )

    # ---------- TEST 3：数据库层兜底 ----------
    print("\n" + "-" * 68)
    print("TEST 3   生成列唯一索引 uk_active_driver 兜底")
    print("-" * 68)

    winner_driver = order["driver_id"]
    backstop = test_active_driver_backstop(winner_driver, ids["passenger_id"], ids["region"])
    print("  新订单 #%s，已向司机 #%s 发出邀请：%s 条"
          % (backstop["order_id"], winner_driver, backstop["invited"]))
    print("  该司机尝试接第二单 -> %s" % backstop["code"])
    print("  说明：%s" % backstop["msg"])

    test3_pass = backstop["invited"] >= 1 and backstop["code"] != "OK"

    # ---------- 汇总 ----------
    print("\n" + "=" * 68)
    print("测试结果")
    print("=" * 68)
    print("  TEST 1  并发抢占唯一性      : %s" % ("通过" if test1_pass else "失败"))
    print("  TEST 2  状态一致性          : %s" % ("通过" if test2_pass else "失败"))
    print("  TEST 3  数据库层兜底        : %s" % ("通过" if test3_pass else "失败"))

    all_pass = test1_pass and test2_pass and test3_pass
    print("\n  总体：%s" % ("全部通过" if all_pass else "存在失败项"))

    if "--cleanup" in sys.argv:
        cleanup(ids)
        print("  已清理本次测试数据。")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
