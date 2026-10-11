# -*- coding: utf-8 -*-
"""统计报表数据访问（管理端仪表盘 + 选做的区域供需统计）。

⚠️ 本机 sql_mode 含 ONLY_FULL_GROUP_BY：
   SELECT 中的非聚合列必须完整出现在 GROUP BY 里，不能用「随便取一行」的写法。
   本模块所有聚合查询都已按此约束编写。
"""
from __future__ import annotations

from typing import Any, Dict, List

from db.connection import transaction


def overview(conn) -> Dict[str, Any]:
    """总体概览：各实体规模、订单状态分布、营收合计。"""
    result: Dict[str, Any] = {}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT"
            " (SELECT COUNT(*) FROM `passenger`) AS `passengers`,"
            " (SELECT COUNT(*) FROM `driver`)    AS `drivers`,"
            " (SELECT COUNT(*) FROM `vehicle`)   AS `vehicles`,"
            " (SELECT COUNT(*) FROM `ride_order`) AS `orders`,"
            " (SELECT COUNT(*) FROM `order_review`) AS `reviews`,"
            " (SELECT COUNT(*) FROM `order_track`)  AS `track_points`,"
            " (SELECT COUNT(*) FROM `driver` WHERE `online_status` = 1) AS `drivers_online`,"
            " (SELECT COUNT(*) FROM `vehicle` WHERE `operate_status` = 1) AS `vehicles_ok`"
        )
        result.update(cur.fetchone() or {})

        cur.execute(
            "SELECT"
            " COUNT(*) AS `total`,"
            " SUM(CASE WHEN `status` = 'completed' THEN 1 ELSE 0 END) AS `completed`,"
            " SUM(CASE WHEN `status` = 'cancelled' THEN 1 ELSE 0 END) AS `cancelled`,"
            " SUM(CASE WHEN `status` IN ('pending','accepted','arrived','in_progress')"
            "          THEN 1 ELSE 0 END) AS `active`,"
            " ROUND(SUM(IFNULL(`fare_amount`, 0)), 2) AS `revenue`,"
            " ROUND(AVG(NULLIF(`fare_amount`, 0)), 2) AS `avg_fare`,"
            " ROUND(AVG(NULLIF(`distance_km`, 0)), 2) AS `avg_distance`"
            "  FROM `ride_order`"
        )
        result.update(cur.fetchone() or {})

    return result


def orders_by_status(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `status`, COUNT(*) AS `n` FROM `ride_order`"
            " GROUP BY `status` ORDER BY `n` DESC"
        )
        return list(cur.fetchall())


def orders_by_car_type(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ct.`car_type_id`, ct.`type_name`,"
            "       COUNT(o.`order_id`) AS `order_count`,"
            "       SUM(CASE WHEN o.`status` = 'completed' THEN 1 ELSE 0 END) AS `completed`,"
            "       ROUND(SUM(IFNULL(o.`fare_amount`, 0)), 2) AS `revenue`,"
            "       ROUND(AVG(NULLIF(o.`fare_amount`, 0)), 2) AS `avg_fare`"
            "  FROM `car_type` ct"
            "  LEFT JOIN `ride_order` o ON o.`car_type_id` = ct.`car_type_id`"
            " GROUP BY ct.`car_type_id`, ct.`type_name`"
            " ORDER BY `order_count` DESC"
        )
        return list(cur.fetchall())


def orders_by_hour(conn) -> List[Dict[str, Any]]:
    """按小时分布（高峰时段分析）。GROUP BY 用表达式，非聚合列只有该表达式本身。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT HOUR(`created_at`) AS `hour`, COUNT(*) AS `n`"
            "  FROM `ride_order`"
            " GROUP BY HOUR(`created_at`)"
            " ORDER BY `hour`"
        )
        return list(cur.fetchall())


def orders_by_date(conn, days: int = 30) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DATE(`created_at`) AS `day`, COUNT(*) AS `n`,"
            "       SUM(CASE WHEN `status` = 'completed' THEN 1 ELSE 0 END) AS `completed`,"
            "       ROUND(SUM(IFNULL(`fare_amount`, 0)), 2) AS `revenue`"
            "  FROM `ride_order`"
            " WHERE `created_at` >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"
            " GROUP BY DATE(`created_at`)"
            " ORDER BY `day`",
            (days,),
        )
        return list(cur.fetchall())


def region_supply_demand(conn, days: int = 7) -> List[Dict[str, Any]]:
    """选做：区域供需统计。

    需求侧 = 最近 N 天的订单量；供给侧 = 当前具备接单资格的司机数。
    用两个派生表 LEFT JOIN 到区域列表上，避免因某区域无订单/无司机而丢行。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT"
            "  reg.`region`                                   AS `region`,"
            "  IFNULL(d.`recent_orders`, 0)                   AS `recent_orders`,"
            "  IFNULL(d.`completed_orders`, 0)                AS `completed_orders`,"
            "  IFNULL(d.`cancelled_orders`, 0)                AS `cancelled_orders`,"
            "  IFNULL(s.`online_drivers`, 0)                  AS `online_drivers`,"
            "  IFNULL(s.`avg_score`, 0)                       AS `avg_score`,"
            "  CASE WHEN IFNULL(s.`online_drivers`, 0) = 0 THEN NULL"
            "       ELSE ROUND(IFNULL(d.`recent_orders`, 0) / s.`online_drivers`, 2)"
            "  END                                            AS `demand_supply_ratio`"
            " FROM ("
            "   SELECT `region` FROM `driver`"
            "   UNION"
            "   SELECT `origin_region` FROM `ride_order`"
            " ) reg"
            " LEFT JOIN ("
            "   SELECT `origin_region` AS `region`, COUNT(*) AS `recent_orders`,"
            "          SUM(CASE WHEN `status` = 'completed' THEN 1 ELSE 0 END) AS `completed_orders`,"
            "          SUM(CASE WHEN `status` = 'cancelled' THEN 1 ELSE 0 END) AS `cancelled_orders`"
            "     FROM `ride_order`"
            "    WHERE `created_at` >= DATE_SUB(NOW(), INTERVAL %s DAY)"
            "    GROUP BY `origin_region`"
            " ) d ON d.`region` = reg.`region`"
            " LEFT JOIN ("
            "   SELECT `region`, COUNT(*) AS `online_drivers`,"
            "          ROUND(AVG(`service_score`), 2) AS `avg_score`"
            "     FROM `v_available_drivers`"
            "    GROUP BY `region`"
            " ) s ON s.`region` = reg.`region`"
            " ORDER BY `recent_orders` DESC, `region`",
            (days,),
        )
        return list(cur.fetchall())


def region_orders(conn) -> List[Dict[str, Any]]:
    """区域订单量与取消率（含当前在线可用司机数）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT"
            "  o.`origin_region`                              AS `region`,"
            "  COUNT(*)                                       AS `order_count`,"
            "  SUM(CASE WHEN o.`status` = 'completed' THEN 1 ELSE 0 END) AS `completed`,"
            "  SUM(CASE WHEN o.`status` = 'cancelled' THEN 1 ELSE 0 END) AS `cancelled`,"
            "  ROUND(SUM(CASE WHEN o.`status` = 'cancelled' THEN 1 ELSE 0 END)"
            "        * 100.0 / COUNT(*), 2)                   AS `cancel_rate`,"
            "  ROUND(AVG(NULLIF(o.`distance_km`, 0)), 2)      AS `avg_distance`,"
            "  ROUND(SUM(IFNULL(o.`fare_amount`, 0)), 2)      AS `revenue`,"
            "  (SELECT COUNT(*) FROM `v_available_drivers` ad"
            "    WHERE ad.`region` = o.`origin_region`)       AS `online_drivers`"
            " FROM `ride_order` o"
            " GROUP BY o.`origin_region`"
            " ORDER BY `order_count` DESC"
        )
        return list(cur.fetchall())


def top_drivers(conn, limit: int = 10) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.`driver_id`, d.`real_name`, d.`region`, d.`service_score`,"
            "       COUNT(o.`order_id`) AS `completed_orders`,"
            "       ROUND(SUM(o.`fare_amount`), 2) AS `revenue`,"
            "       ROUND(AVG(o.`fare_amount`), 2) AS `avg_fare`,"
            "       ROUND(AVG(o.`distance_km`), 2) AS `avg_distance`"
            "  FROM `driver` d"
            "  JOIN `ride_order` o ON o.`driver_id` = d.`driver_id`"
            "                     AND o.`status` = 'completed'"
            " GROUP BY d.`driver_id`, d.`real_name`, d.`region`, d.`service_score`"
            " ORDER BY `completed_orders` DESC, `revenue` DESC"
            " LIMIT %s",
            (limit,),
        )
        return list(cur.fetchall())


def dispatch_stats(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `status`, COUNT(*) AS `n` FROM `order_dispatch`"
            " GROUP BY `status` ORDER BY `n` DESC"
        )
        return list(cur.fetchall())


def stage_durations(conn) -> Dict[str, Any]:
    """各阶段平均耗时（来自 v_order_status_duration）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS `sample_count`,"
            "       ROUND(AVG(`wait_accept_sec`), 1) AS `avg_wait_accept`,"
            "       ROUND(AVG(`pickup_sec`), 1)      AS `avg_pickup`,"
            "       ROUND(AVG(`trip_sec`), 1)        AS `avg_trip`,"
            "       ROUND(AVG(`total_sec`), 1)       AS `avg_total`,"
            "       ROUND(MAX(`wait_accept_sec`), 1) AS `max_wait_accept`"
            "  FROM `v_order_status_duration`"
            " WHERE `t_completed` IS NOT NULL"
        )
        return cur.fetchone() or {}


def review_summary(conn) -> Dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS `total`,"
            "       ROUND(AVG(`score`), 2) AS `avg_score`,"
            "       SUM(CASE WHEN `score` >= 4 THEN 1 ELSE 0 END) AS `good`,"
            "       SUM(CASE WHEN `score` <= 2 THEN 1 ELSE 0 END) AS `bad`"
            "  FROM `order_review`"
        )
        return cur.fetchone() or {}


def expiring_licenses(conn, days: int = 30) -> List[Dict[str, Any]]:
    """选做：证照到期提醒。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM `v_expiring_licenses`"
            " WHERE `days_left` <= %s ORDER BY `days_left`",
            (days,),
        )
        return list(cur.fetchall())


def data_overview(conn) -> List[Dict[str, Any]]:
    """全库对象规模，用于报告与演示。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT '乘客' AS `name`, COUNT(*) AS `n` FROM `passenger`"
            " UNION ALL SELECT '司机', COUNT(*) FROM `driver`"
            " UNION ALL SELECT '车辆', COUNT(*) FROM `vehicle`"
            " UNION ALL SELECT '常用地址', COUNT(*) FROM `common_address`"
            " UNION ALL SELECT '计价规则', COUNT(*) FROM `pricing_rule`"
            " UNION ALL SELECT '订单', COUNT(*) FROM `ride_order`"
            " UNION ALL SELECT '状态日志', COUNT(*) FROM `ride_order_status_log`"
            " UNION ALL SELECT '派单记录', COUNT(*) FROM `order_dispatch`"
            " UNION ALL SELECT '轨迹点', COUNT(*) FROM `order_track`"
            " UNION ALL SELECT '评价', COUNT(*) FROM `order_review`"
            " UNION ALL SELECT '车辆定位', COUNT(*) FROM `vehicle_location_current`"
        )
        return list(cur.fetchall())
