# -*- coding: utf-8 -*-
"""计价规则表数据访问。

【版本化纪律】改价绝不 UPDATE 已生效行的单价列。正确做法见 create_new_version：
    1) 把该车型当前有效的旧行 effective_to 设为新行生效时刻、status 置 0；
    2) 插入一条 version+1 的新行。
这样历史订单引用的旧规则行永远不会被篡改。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from db.connection import transaction


def list_current(conn) -> List[Dict[str, Any]]:
    """各车型当前生效的规则（走 v_pricing_current 视图）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.*, ct.`type_name`"
            "  FROM `v_pricing_current` r"
            "  JOIN `car_type` ct ON ct.`car_type_id` = r.`car_type_id`"
            " ORDER BY r.`car_type_id`"
        )
        return list(cur.fetchall())


def list_all(conn, car_type_id: Optional[int] = None,
             limit: int = 200) -> List[Dict[str, Any]]:
    """全部规则（含历史版本），供管理端查看版本演进。"""
    sql = (
        "SELECT r.*, ct.`type_name`, a.`real_name` AS `creator_name`,"
        "       CASE WHEN r.`effective_from` <= NOW()"
        "                 AND (r.`effective_to` IS NULL OR r.`effective_to` > NOW())"
        "                 AND r.`status` = 1"
        "            THEN 1 ELSE 0 END AS `is_current`,"
        "       (SELECT COUNT(*) FROM `ride_order` o"
        "         WHERE o.`pricing_rule_id` = r.`rule_id`) AS `order_count`"
        "  FROM `pricing_rule` r"
        "  JOIN `car_type` ct ON ct.`car_type_id` = r.`car_type_id`"
        "  LEFT JOIN `admin` a ON a.`admin_id` = r.`created_by`"
    )
    params: List[Any] = []
    if car_type_id:
        sql += " WHERE r.`car_type_id` = %s"
        params.append(car_type_id)
    sql += " ORDER BY r.`car_type_id`, r.`version` DESC LIMIT %s"
    params.append(limit)

    with conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        return list(cur.fetchall())


def get_current_for_car_type(conn, car_type_id: int) -> Optional[Dict[str, Any]]:
    """取某车型当前生效规则。下单时用它做价格快照。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `v_pricing_current`"
            " WHERE `car_type_id` = %s ORDER BY `version` DESC LIMIT 1",
            (car_type_id,),
        )
        return cur.fetchone()


def next_version(conn, car_type_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT IFNULL(MAX(`version`), 0) + 1 AS v FROM `pricing_rule`"
            " WHERE `car_type_id` = %s",
            (car_type_id,),
        )
        return int(cur.fetchone()["v"])


def create_new_version(conn, car_type_id: int, rule_name: str,
                       base_price: float, base_distance_km: float,
                       per_km_price: float, per_min_price: float,
                       min_price: float, created_by: Optional[int],
                       effective_from: Optional[datetime] = None) -> Dict[str, Any]:
    """新增一个版本的计价规则，并关闭同车型的旧版本（单个事务内完成）。

    返回 {"rule_id":..., "version":..., "closed": 关闭的旧规则数}
    """
    if effective_from is None:
        effective_from = datetime.now()

    with transaction(conn) as cur:
        # 1) 关闭该车型当前仍然生效的旧规则
        cur.execute(
            "UPDATE `pricing_rule`"
            "   SET `effective_to` = %s, `status` = 0"
            " WHERE `car_type_id` = %s AND `status` = 1"
            "   AND `effective_from` < %s"
            "   AND (`effective_to` IS NULL OR `effective_to` > %s)",
            (effective_from, car_type_id, effective_from, effective_from),
        )
        closed = cur.rowcount

        # 2) 取新版本号
        cur.execute(
            "SELECT IFNULL(MAX(`version`), 0) + 1 AS v FROM `pricing_rule`"
            " WHERE `car_type_id` = %s",
            (car_type_id,),
        )
        version = int(cur.fetchone()["v"])

        # 3) 插入新版本行
        cur.execute(
            "INSERT INTO `pricing_rule`"
            " (`car_type_id`,`rule_name`,`version`,`base_price`,`base_distance_km`,"
            "  `per_km_price`,`per_min_price`,`min_price`,`effective_from`,`effective_to`,"
            "  `status`,`created_by`)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,1,%s)",
            (car_type_id, rule_name, version, base_price, base_distance_km,
             per_km_price, per_min_price, min_price, effective_from, created_by),
        )
        rule_id = cur.lastrowid

    return {"rule_id": rule_id, "version": version, "closed": closed,
            "effective_from": effective_from}


def toggle_status(conn, rule_id: int, status: int) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `pricing_rule` SET `status` = %s WHERE `rule_id` = %s",
            (status, rule_id),
        )


def get_by_id(conn, rule_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.*, ct.`type_name` FROM `pricing_rule` r"
            "  JOIN `car_type` ct ON ct.`car_type_id` = r.`car_type_id`"
            " WHERE r.`rule_id` = %s",
            (rule_id,),
        )
        return cur.fetchone()
