# -*- coding: utf-8 -*-
"""派单邀请记录表数据访问。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


def list_driver_invites(conn, driver_id: int,
                        status: Optional[str] = None,
                        limit: int = 50) -> List[Dict[str, Any]]:
    """司机收到的邀请列表（含订单详情，供司机端「待接单」页面展示）。"""
    sql = (
        "SELECT od.`dispatch_id`, od.`order_id`, od.`status` AS `invite_status`,"
        "       od.`batch_no`, od.`invited_at`, od.`expire_at`, od.`distance_km`,"
        "       o.`order_no`, o.`origin_address`, o.`dest_address`,"
        "       o.`origin_region`, o.`car_type_name`, o.`status` AS `order_status`,"
        "       o.`status_text`, o.`passenger_name`, o.`est_fare`, o.`created_at`,"
        "       o.`passenger_remark`"
        "  FROM `order_dispatch` od"
        "  JOIN `v_order_detail` o ON o.`order_id` = od.`order_id`"
        " WHERE od.`driver_id` = %s"
    )
    params: List[Any] = [driver_id]
    if status:
        sql += " AND od.`status` = %s"
        params.append(status)
    sql += " ORDER BY od.`invited_at` DESC LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def list_order_dispatches(conn, order_id: int) -> List[Dict[str, Any]]:
    """某订单的全部派单记录（管理端与乘客端可展示派单过程）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT od.*, d.`real_name` AS `driver_name`, d.`phone` AS `driver_phone`,"
            "       d.`service_score`"
            "  FROM `order_dispatch` od"
            "  JOIN `driver` d ON d.`driver_id` = od.`driver_id`"
            " WHERE od.`order_id` = %s"
            " ORDER BY od.`batch_no`, od.`invited_at`",
            (order_id,),
        )
        return list(cur.fetchall())


def get_invite(conn, order_id: int, driver_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `order_dispatch`"
            " WHERE `order_id` = %s AND `driver_id` = %s",
            (order_id, driver_id),
        )
        return cur.fetchone()


def count_pending_invites(conn, driver_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS `n` FROM `order_dispatch`"
            " WHERE `driver_id` = %s AND `status` = 'invited' AND `expire_at` > NOW()",
            (driver_id,),
        )
        return int(cur.fetchone()["n"])


def reject(conn, order_id: int, driver_id: int, reason: Optional[str] = None) -> int:
    """司机拒单。

    注意：拒单**不改变订单状态** —— 订单仍然是「待接单」，只是不派给这个司机。
    这正是需要独立 order_dispatch 表的原因：拒单这个动作在订单状态机里无处安放。
    带 WHERE status='invited' 守卫，防止重复拒单或拒已接的单。
    """
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `order_dispatch`"
            "   SET `status` = 'rejected', `responded_at` = NOW(),"
            "       `remark` = %s"
            " WHERE `order_id` = %s AND `driver_id` = %s AND `status` = 'invited'",
            (reason, order_id, driver_id),
        )


def mark_others_cancelled(conn, order_id: int, except_driver_id: int) -> int:
    """某司机接单后，把同订单其他仍待应答的邀请全部取消。"""
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `order_dispatch`"
            "   SET `status` = 'cancelled', `responded_at` = NOW()"
            " WHERE `order_id` = %s AND `driver_id` <> %s AND `status` = 'invited'",
            (order_id, except_driver_id),
        )


def stats_by_status(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `status`, COUNT(*) AS `n` FROM `order_dispatch`"
            " GROUP BY `status` ORDER BY `n` DESC"
        )
        return list(cur.fetchall())
