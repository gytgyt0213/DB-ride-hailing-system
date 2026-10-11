# -*- coding: utf-8 -*-
"""司机档案表数据访问。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


def get_by_id(conn, driver_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.`driver_id`, d.`account_id`, d.`real_name`, d.`phone`,"
            "       d.`id_card_no`, d.`driver_license_no`, d.`license_expire_date`,"
            "       d.`region`, d.`online_status`, d.`audit_status`,"
            "       d.`service_score`, d.`review_count`, d.`status`, d.`created_at`,"
            "       a.`username`, a.`status` AS `account_status`"
            "  FROM `driver` d"
            "  JOIN `account` a ON a.`account_id` = d.`account_id`"
            " WHERE d.`driver_id` = %s",
            (driver_id,),
        )
        return cur.fetchone()


def get_by_account(conn, account_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `driver_id`,`account_id`,`real_name`,`phone`,`region`,"
            "       `online_status`,`audit_status`,`service_score`,`review_count`,`status`"
            "  FROM `driver` WHERE `account_id` = %s",
            (account_id,),
        )
        return cur.fetchone()


def create(conn, account_id: int, real_name: str, phone: str, region: str,
           id_card_no: Optional[str] = None,
           driver_license_no: Optional[str] = None,
           license_expire_date=None, audit_status: int = 1) -> int:
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO `driver`"
            " (`account_id`,`real_name`,`phone`,`id_card_no`,`driver_license_no`,"
            "  `license_expire_date`,`region`,`online_status`,`audit_status`,"
            "  `service_score`,`review_count`,`status`)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,5.00,0,1)",
            (account_id, real_name, phone, id_card_no, driver_license_no,
             license_expire_date, region, audit_status),
        )
        return cur.lastrowid


def set_online(conn, driver_id: int, online: int) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `driver` SET `online_status` = %s WHERE `driver_id` = %s",
            (online, driver_id),
        )


def update_profile(conn, driver_id: int, real_name: str, phone: str,
                   region: str, id_card_no: Optional[str],
                   driver_license_no: Optional[str], license_expire_date) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `driver`"
            "   SET `real_name`=%s, `phone`=%s, `region`=%s, `id_card_no`=%s,"
            "       `driver_license_no`=%s, `license_expire_date`=%s"
            " WHERE `driver_id`=%s",
            (real_name, phone, region, id_card_no, driver_license_no,
             license_expire_date, driver_id),
        )


def set_audit_status(conn, driver_id: int, audit_status: int) -> int:
    """选做：资质审核。驳回时一并强制下线，避免不合格司机继续接单。"""
    with transaction(conn) as cur:
        if audit_status == 1:
            return cur.execute(
                "UPDATE `driver` SET `audit_status` = %s WHERE `driver_id` = %s",
                (audit_status, driver_id),
            )
        return cur.execute(
            "UPDATE `driver` SET `audit_status` = %s, `online_status` = 0"
            " WHERE `driver_id` = %s",
            (audit_status, driver_id),
        )


def set_status(conn, driver_id: int, status: int) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `driver` SET `status` = %s, `online_status` = 0 WHERE `driver_id` = %s",
            (status, driver_id),
        )


def list_all(conn, keyword: Optional[str] = None,
             audit_status: Optional[int] = None,
             limit: int = 200) -> List[Dict[str, Any]]:
    sql = (
        "SELECT d.`driver_id`, d.`real_name`, d.`phone`, d.`region`,"
        "       d.`online_status`, d.`audit_status`, d.`service_score`,"
        "       d.`review_count`, d.`status`, d.`license_expire_date`,"
        "       d.`created_at`, a.`username`, a.`status` AS `account_status`,"
        "       (SELECT COUNT(*) FROM `vehicle` v"
        "         WHERE v.`driver_id` = d.`driver_id`) AS `vehicle_count`,"
        "       (SELECT COUNT(*) FROM `vehicle` v"
        "         WHERE v.`driver_id` = d.`driver_id` AND v.`operate_status` = 1)"
        "           AS `available_vehicle_count`,"
        "       (SELECT COUNT(*) FROM `ride_order` o"
        "         WHERE o.`driver_id` = d.`driver_id` AND o.`status` = 'completed')"
        "           AS `completed_orders`"
        "  FROM `driver` d"
        "  JOIN `account` a ON a.`account_id` = d.`account_id`"
    )
    where: List[str] = []
    params: List[Any] = []
    if keyword:
        where.append("(d.`real_name` LIKE %s OR d.`phone` LIKE %s OR a.`username` LIKE %s)")
        like = "%" + keyword + "%"
        params.extend([like, like, like])
    if audit_status is not None:
        where.append("d.`audit_status` = %s")
        params.append(audit_status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY d.`driver_id` LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def list_available(conn, region: Optional[str] = None) -> List[Dict[str, Any]]:
    """从 v_available_drivers 视图取当前具备接单资格的司机。"""
    sql = ("SELECT * FROM `v_available_drivers`")
    params: List[Any] = []
    if region:
        sql += " WHERE `region` = %s"
        params.append(region)
    sql += " ORDER BY `service_score` DESC, `driver_id`"

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def list_expiring_licenses(conn, days: int = 30) -> List[Dict[str, Any]]:
    """选做：证照到期提醒。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `v_expiring_licenses`"
            " WHERE `days_left` <= %s ORDER BY `days_left`",
            (days,),
        )
        return list(cur.fetchall())
