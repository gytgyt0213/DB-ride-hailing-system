# -*- coding: utf-8 -*-
"""乘客常用地址表数据访问。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


def list_by_passenger(conn, passenger_id: int) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `address_id`,`label`,`address_name`,`longitude`,`latitude`,"
            "       `is_default`,`created_at`"
            "  FROM `common_address` WHERE `passenger_id` = %s"
            " ORDER BY `is_default` DESC, `address_id`",
            (passenger_id,),
        )
        return list(cur.fetchall())


def get_by_id(conn, address_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `common_address` WHERE `address_id` = %s",
            (address_id,),
        )
        return cur.fetchone()


def create(conn, passenger_id: int, label: str, address_name: str,
           longitude: Optional[float], latitude: Optional[float],
           is_default: int = 0) -> int:
    with transaction(conn) as cur:
        if is_default:
            cur.execute(
                "UPDATE `common_address` SET `is_default` = 0 WHERE `passenger_id` = %s",
                (passenger_id,),
            )
        cur.execute(
            "INSERT INTO `common_address`"
            " (`passenger_id`,`label`,`address_name`,`longitude`,`latitude`,`is_default`)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (passenger_id, label, address_name, longitude, latitude, is_default),
        )
        return cur.lastrowid


def update(conn, address_id: int, passenger_id: int, label: str,
           address_name: str, longitude: Optional[float],
           latitude: Optional[float], is_default: int) -> int:
    with transaction(conn) as cur:
        if is_default:
            cur.execute(
                "UPDATE `common_address` SET `is_default` = 0"
                " WHERE `passenger_id` = %s AND `address_id` <> %s",
                (passenger_id, address_id),
            )
        return cur.execute(
            "UPDATE `common_address`"
            "   SET `label`=%s, `address_name`=%s, `longitude`=%s,"
            "       `latitude`=%s, `is_default`=%s"
            " WHERE `address_id`=%s AND `passenger_id`=%s",
            (label, address_name, longitude, latitude, is_default,
             address_id, passenger_id),
        )


def delete(conn, address_id: int, passenger_id: int) -> int:
    """删除地址。带上 passenger_id 条件，防止越权删他人的地址。"""
    with transaction(conn) as cur:
        return cur.execute(
            "DELETE FROM `common_address`"
            " WHERE `address_id` = %s AND `passenger_id` = %s",
            (address_id, passenger_id),
        )
