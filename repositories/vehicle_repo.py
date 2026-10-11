# -*- coding: utf-8 -*-
"""车辆表数据访问。

「车辆处于可运营状态才能接单」这条规则在本层只负责**筛选**（operate_status=1），
真正的并发安全保障在 sp_accept_order 的事务内用 FOR UPDATE 完成。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


def get_by_id(conn, vehicle_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT v.*, ct.`type_name`, d.`real_name` AS `driver_name`"
            "  FROM `vehicle` v"
            "  JOIN `car_type` ct ON ct.`car_type_id` = v.`car_type_id`"
            "  JOIN `driver`   d  ON d.`driver_id`   = v.`driver_id`"
            " WHERE v.`vehicle_id` = %s",
            (vehicle_id,),
        )
        return cur.fetchone()


def list_by_driver(conn, driver_id: int) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT v.*, ct.`type_name`"
            "  FROM `vehicle` v"
            "  JOIN `car_type` ct ON ct.`car_type_id` = v.`car_type_id`"
            " WHERE v.`driver_id` = %s"
            " ORDER BY v.`vehicle_id`",
            (driver_id,),
        )
        return list(cur.fetchall())


def list_all(conn, keyword: Optional[str] = None,
             operate_status: Optional[int] = None,
             driver_id: Optional[int] = None,
             limit: int = 200) -> List[Dict[str, Any]]:
    sql = (
        "SELECT v.*, ct.`type_name`, d.`real_name` AS `driver_name`, d.`phone` AS `driver_phone`"
        "  FROM `vehicle` v"
        "  JOIN `car_type` ct ON ct.`car_type_id` = v.`car_type_id`"
        "  JOIN `driver`   d  ON d.`driver_id`   = v.`driver_id`"
    )
    where: List[str] = []
    params: List[Any] = []
    if keyword:
        where.append("(v.`plate_no` LIKE %s OR v.`brand` LIKE %s"
                     " OR v.`model` LIKE %s OR d.`real_name` LIKE %s)")
        like = "%" + keyword + "%"
        params.extend([like, like, like, like])
    if operate_status is not None:
        where.append("v.`operate_status` = %s")
        params.append(operate_status)
    if driver_id is not None:
        where.append("v.`driver_id` = %s")
        params.append(driver_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY v.`vehicle_id` LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def create(conn, driver_id: int, car_type_id: int, plate_no: str,
           brand: Optional[str], model: Optional[str], color: Optional[str],
           seat_count: int, operate_status: int = 1,
           vehicle_license_no: Optional[str] = None,
           register_date=None, insurance_expire_date=None,
           annual_check_expire_date=None) -> int:
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO `vehicle`"
            " (`driver_id`,`car_type_id`,`plate_no`,`brand`,`model`,`color`,"
            "  `seat_count`,`operate_status`,`vehicle_license_no`,`register_date`,"
            "  `insurance_expire_date`,`annual_check_expire_date`)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (driver_id, car_type_id, plate_no, brand, model, color, seat_count,
             operate_status, vehicle_license_no, register_date,
             insurance_expire_date, annual_check_expire_date),
        )
        return cur.lastrowid


def update(conn, vehicle_id: int, car_type_id: int, plate_no: str,
           brand: Optional[str], model: Optional[str], color: Optional[str],
           seat_count: int, operate_status: int,
           vehicle_license_no: Optional[str], register_date=None,
           insurance_expire_date=None, annual_check_expire_date=None) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `vehicle`"
            "   SET `car_type_id`=%s, `plate_no`=%s, `brand`=%s, `model`=%s,"
            "       `color`=%s, `seat_count`=%s, `operate_status`=%s,"
            "       `vehicle_license_no`=%s, `register_date`=%s,"
            "       `insurance_expire_date`=%s, `annual_check_expire_date`=%s"
            " WHERE `vehicle_id`=%s",
            (car_type_id, plate_no, brand, model, color, seat_count, operate_status,
             vehicle_license_no, register_date, insurance_expire_date,
             annual_check_expire_date, vehicle_id),
        )


def set_operate_status(conn, vehicle_id: int, operate_status: int) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `vehicle` SET `operate_status` = %s WHERE `vehicle_id` = %s",
            (operate_status, vehicle_id),
        )


def plate_exists(conn, plate_no: str, exclude_id: Optional[int] = None) -> bool:
    sql = "SELECT 1 FROM `vehicle` WHERE `plate_no` = %s"
    params: List[Any] = [plate_no]
    if exclude_id:
        sql += " AND `vehicle_id` <> %s"
        params.append(exclude_id)
    sql += " LIMIT 1"
    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchone() is not None


def list_car_types(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `car_type_id`,`type_code`,`type_name`,`seat_count`,`status`"
            "  FROM `car_type` ORDER BY `car_type_id`"
        )
        return list(cur.fetchall())
