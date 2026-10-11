# -*- coding: utf-8 -*-
"""订单评价表数据访问（选做：乘客评价与司机服务分）。

注意：每次新增评价都会触发 trg_review_after_insert，
由触发器增量维护 driver.service_score 与 driver.review_count。
本层不做该维护，避免与触发器重复计算。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


def get_by_order(conn, order_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.*, d.`real_name` AS `driver_name`, p.`real_name` AS `passenger_name`"
            "  FROM `order_review` r"
            "  JOIN `driver`    d ON d.`driver_id`    = r.`driver_id`"
            "  JOIN `passenger` p ON p.`passenger_id` = r.`passenger_id`"
            " WHERE r.`order_id` = %s",
            (order_id,),
        )
        return cur.fetchone()


def create(conn, order_id: int, passenger_id: int, driver_id: int,
           score: int, content: Optional[str], tags: Optional[str]) -> int:
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO `order_review`"
            " (`order_id`,`passenger_id`,`driver_id`,`score`,`content`,`tags`)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (order_id, passenger_id, driver_id, score, content, tags),
        )
        return cur.lastrowid


def list_by_driver(conn, driver_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.*, o.`order_no`, o.`origin_address`, o.`dest_address`,"
            "       p.`real_name` AS `passenger_name`"
            "  FROM `order_review` r"
            "  JOIN `ride_order` o ON o.`order_id` = r.`order_id`"
            "  JOIN `passenger`  p ON p.`passenger_id` = r.`passenger_id`"
            " WHERE r.`driver_id` = %s"
            " ORDER BY r.`created_at` DESC LIMIT %s",
            (driver_id, limit),
        )
        return list(cur.fetchall())


def list_recent(conn, limit: int = 50) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT r.`review_id`, r.`order_id`, r.`score`, r.`content`, r.`tags`,"
            "       r.`created_at`, o.`order_no`,"
            "       p.`real_name` AS `passenger_name`, d.`real_name` AS `driver_name`"
            "  FROM `order_review` r"
            "  JOIN `ride_order` o ON o.`order_id` = r.`order_id`"
            "  JOIN `passenger`  p ON p.`passenger_id` = r.`passenger_id`"
            "  JOIN `driver`     d ON d.`driver_id` = r.`driver_id`"
            " ORDER BY r.`created_at` DESC LIMIT %s",
            (limit,),
        )
        return list(cur.fetchall())


def driver_scores(conn, min_reviews: int = 1, limit: int = 50) -> List[Dict[str, Any]]:
    """司机服务分排行（走 v_driver_service_score 视图）。

    该视图同时给出「触发器维护的冗余值」与「实时聚合值」，
    可用于校验触发器维护是否正确。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `v_driver_service_score`"
            " WHERE `actual_count` >= %s"
            " ORDER BY `stored_score` DESC, `actual_count` DESC LIMIT %s",
            (min_reviews, limit),
        )
        return list(cur.fetchall())


def score_distribution(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `score`, COUNT(*) AS `n` FROM `order_review`"
            " GROUP BY `score` ORDER BY `score` DESC"
        )
        return list(cur.fetchall())
