# -*- coding: utf-8 -*-
"""订单表数据访问（查询 + 简单的状态推进）。

【分工】
  * 涉及金额快照、并发抢占、结算的复杂写操作走存储过程（见 services/order_service.py）
  * 本模块只做查询，以及「到达 / 开始行程」这类带状态守卫的简单 UPDATE
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


# 所有列表/详情查询统一走 v_order_detail 视图，
# 由数据库负责多表连接与状态中文映射，应用层不再重复写 JOIN。

def get_detail(conn, order_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM `v_order_detail` WHERE `order_id` = %s", (order_id,))
        return cur.fetchone()


def get_status_log(conn, order_id: int) -> List[Dict[str, Any]]:
    """订单状态变更时间线。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `log_id`,`from_status`,`to_status`,`operator_type`,`operator_id`,"
            "       `changed_at`,`remark`"
            "  FROM `ride_order_status_log`"
            " WHERE `order_id` = %s ORDER BY `log_id`",
            (order_id,),
        )
        return list(cur.fetchall())


def get_duration(conn, order_id: int) -> Optional[Dict[str, Any]]:
    """各阶段耗时（来自 v_order_status_duration）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `v_order_status_duration` WHERE `order_id` = %s",
            (order_id,),
        )
        return cur.fetchone()


def list_by_passenger(conn, passenger_id: int,
                      status: Optional[str] = None,
                      limit: int = 50) -> List[Dict[str, Any]]:
    sql = ("SELECT * FROM `v_order_detail` WHERE `passenger_id` = %s")
    params: List[Any] = [passenger_id]
    if status:
        sql += " AND `status` = %s"
        params.append(status)
    sql += " ORDER BY `created_at` DESC LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def list_by_driver(conn, driver_id: int,
                   statuses: Optional[List[str]] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM `v_order_detail` WHERE `driver_id` = %s"
    params: List[Any] = [driver_id]
    if statuses:
        placeholders = ", ".join(["%s"] * len(statuses))
        sql += " AND `status` IN (%s)" % placeholders
        params.extend(statuses)
    sql += " ORDER BY `created_at` DESC LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def get_driver_active_order(conn, driver_id: int) -> Optional[Dict[str, Any]]:
    """司机当前正在执行的订单（进行中类状态）。

    借助 uk_active_driver 唯一索引，一个司机最多只有一条这样的订单。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `v_order_detail`"
            " WHERE `driver_id` = %s"
            "   AND `status` IN ('accepted','arrived','in_progress')"
            " LIMIT 1",
            (driver_id,),
        )
        return cur.fetchone()


def list_all(conn, status: Optional[str] = None,
             keyword: Optional[str] = None,
             region: Optional[str] = None,
             limit: int = 200) -> List[Dict[str, Any]]:
    """管理端订单监控。"""
    sql = "SELECT * FROM `v_order_detail` WHERE 1 = 1"
    params: List[Any] = []
    if status:
        sql += " AND `status` = %s"
        params.append(status)
    if region:
        sql += " AND `origin_region` = %s"
        params.append(region)
    if keyword:
        sql += (" AND (`order_no` LIKE %s OR `passenger_name` LIKE %s"
                " OR `driver_name` LIKE %s OR `origin_address` LIKE %s"
                " OR `dest_address` LIKE %s)")
        like = "%" + keyword + "%"
        params.extend([like] * 5)
    sql += " ORDER BY `created_at` DESC LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def list_pending_pool(conn, region: Optional[str] = None,
                      limit: int = 50) -> List[Dict[str, Any]]:
    """待接单池：所有尚未被接单的订单（管理端/派单用）。"""
    sql = "SELECT * FROM `v_order_detail` WHERE `status` = 'pending'"
    params: List[Any] = []
    if region:
        sql += " AND `origin_region` = %s"
        params.append(region)
    sql += " ORDER BY `created_at` LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


# -------------------------------------------------------------
# 状态推进：带 WHERE status 守卫的条件更新
#
# 每一句都必须在 WHERE 中带上「期望的当前状态」。
# 这是防止并发重复推进的关键：若状态已被别的操作改变，
# 影响行数会是 0，调用方据此判断失败，而不是盲目继续。
# -------------------------------------------------------------

def mark_arrived(conn, order_id: int, driver_id: int) -> int:
    """司机到达上车点：accepted -> arrived。"""
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE `ride_order`"
            "   SET `status` = 'arrived', `arrived_at` = NOW()"
            " WHERE `order_id` = %s AND `driver_id` = %s AND `status` = 'accepted'",
            (order_id, driver_id),
        )
        return cur.rowcount


def mark_started(conn, order_id: int, driver_id: int) -> int:
    """乘客上车、行程开始：arrived -> in_progress。"""
    with transaction(conn) as cur:
        cur.execute(
            "UPDATE `ride_order`"
            "   SET `status` = 'in_progress', `started_at` = NOW()"
            " WHERE `order_id` = %s AND `driver_id` = %s AND `status` = 'arrived'",
            (order_id, driver_id),
        )
        return cur.rowcount


def add_status_log(conn, order_id: int, from_status: Optional[str], to_status: str,
                   operator_type: str, operator_id: Optional[int],
                   remark: Optional[str] = None) -> int:
    """写一条状态变更日志。调用方负责与状态更新放在同一事务中。"""
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO `ride_order_status_log`"
            " (`order_id`,`from_status`,`to_status`,`operator_type`,`operator_id`,`remark`)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (order_id, from_status, to_status, operator_type, operator_id, remark),
        )
        return cur.lastrowid


def append_log_in_tx(cur, order_id: int, from_status: Optional[str], to_status: str,
                     operator_type: str, operator_id: Optional[int],
                     remark: Optional[str] = None) -> None:
    """在已有事务游标中写状态日志（供需要原子性的多步操作使用）。"""
    cur.execute(
        "INSERT INTO `ride_order_status_log`"
        " (`order_id`,`from_status`,`to_status`,`operator_type`,`operator_id`,`remark`)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (order_id, from_status, to_status, operator_type, operator_id, remark),
    )


def get_track(conn, order_id: int, limit: int = 500) -> List[Dict[str, Any]]:
    """订单轨迹点（选做：轨迹回放的数据源）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `track_id`,`longitude`,`latitude`,`speed`,`located_at`"
            "  FROM `order_track` WHERE `order_id` = %s"
            " ORDER BY `located_at` LIMIT %s",
            (order_id, limit),
        )
        return list(cur.fetchall())


def get_track_summary(conn, order_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS `point_count`,"
            "       MIN(`located_at`) AS `first_at`,"
            "       MAX(`located_at`) AS `last_at`,"
            "       ROUND(MAX(`speed`), 2) AS `max_speed`,"
            "       ROUND(AVG(`speed`), 2) AS `avg_speed`"
            "  FROM `order_track` WHERE `order_id` = %s",
            (order_id,),
        )
        return cur.fetchone()


def get_vehicle_location(conn, vehicle_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `vehicle_location_current` WHERE `vehicle_id` = %s",
            (vehicle_id,),
        )
        return cur.fetchone()


def count_by_status(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `status`, COUNT(*) AS `n` FROM `ride_order`"
            " GROUP BY `status` ORDER BY `n` DESC"
        )
        return list(cur.fetchall())
