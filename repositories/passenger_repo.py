# -*- coding: utf-8 -*-
"""乘客档案表数据访问。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


def get_by_id(conn, passenger_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `passenger_id`,`account_id`,`real_name`,`phone`,`gender`,"
            "       `home_region`,`status`,`created_at`"
            "  FROM `passenger` WHERE `passenger_id` = %s",
            (passenger_id,),
        )
        return cur.fetchone()


def get_by_account(conn, account_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `passenger_id`,`account_id`,`real_name`,`phone`,`gender`,"
            "       `home_region`,`status`"
            "  FROM `passenger` WHERE `account_id` = %s",
            (account_id,),
        )
        return cur.fetchone()


def create(conn, account_id: int, real_name: str, phone: str,
           gender: int = 0, home_region: Optional[str] = None) -> int:
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO `passenger`"
            " (`account_id`,`real_name`,`phone`,`gender`,`home_region`,`status`)"
            " VALUES (%s,%s,%s,%s,%s,1)",
            (account_id, real_name, phone, gender, home_region),
        )
        return cur.lastrowid


def update_profile(conn, passenger_id: int, real_name: str, phone: str,
                   gender: int, home_region: Optional[str]) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `passenger`"
            "   SET `real_name`=%s, `phone`=%s, `gender`=%s, `home_region`=%s"
            " WHERE `passenger_id`=%s",
            (real_name, phone, gender, home_region, passenger_id),
        )


def list_all(conn, keyword: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """管理端乘客列表，支持按姓名/手机号模糊搜索。"""
    sql = (
        "SELECT p.`passenger_id`, p.`real_name`, p.`phone`, p.`gender`,"
        "       p.`home_region`, p.`status`, p.`created_at`,"
        "       a.`username`, a.`status` AS `account_status`,"
        "       (SELECT COUNT(*) FROM `ride_order` o WHERE o.`passenger_id` = p.`passenger_id`)"
        "           AS `order_count`"
        "  FROM `passenger` p"
        "  JOIN `account` a ON a.`account_id` = p.`account_id`"
    )
    params: List[Any] = []
    if keyword:
        sql += " WHERE p.`real_name` LIKE %s OR p.`phone` LIKE %s OR a.`username` LIKE %s"
        like = "%" + keyword + "%"
        params.extend([like, like, like])
    sql += " ORDER BY p.`passenger_id` LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())
