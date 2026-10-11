-- =============================================================
-- 03_views.sql
-- 网约车（打车）系统 —— 视图
--
-- 执行方式： mysql -u root -p < sql/03_views.sql
-- 前置条件：已执行 00、01
--
-- 视图的定位：
--   把「多表连接 + 状态中文映射 + 聚合统计」这类重复且易错的查询
--   在数据库层封装一次，应用层直接 SELECT，避免每个页面各写一套 JOIN。
--
-- 注意：本机 sql_mode 含 ONLY_FULL_GROUP_BY，
--       所有视图定义中的非聚合列均已完整出现在 GROUP BY 中。
-- =============================================================

USE `taxi_system`;
SET NAMES utf8mb4;

DROP VIEW IF EXISTS `v_expiring_licenses`;
DROP VIEW IF EXISTS `v_driver_service_score`;
DROP VIEW IF EXISTS `v_order_status_duration`;
DROP VIEW IF EXISTS `v_order_detail`;
DROP VIEW IF EXISTS `v_available_drivers`;
DROP VIEW IF EXISTS `v_pricing_current`;


-- -------------------------------------------------------------
-- 1. v_pricing_current —— 各车型当前生效的计价规则
--    封装「时间有效窗 + 启用状态」判定，下单取价直接查本视图。
-- -------------------------------------------------------------
CREATE VIEW `v_pricing_current` AS
SELECT
    `rule_id`,
    `car_type_id`,
    `rule_name`,
    `version`,
    `base_price`,
    `base_distance_km`,
    `per_km_price`,
    `per_min_price`,
    `min_price`,
    `effective_from`,
    `effective_to`
FROM `pricing_rule`
WHERE `status` = 1
  AND `effective_from` <= NOW()
  AND (`effective_to` IS NULL OR `effective_to` > NOW());


-- -------------------------------------------------------------
-- 2. v_available_drivers —— 具备「当前可接单资格」的司机
--    统一口径：账号启用 + 司机正常 + 在线 + 已审核 + 至少有一辆可运营车辆
--              + 当前没有进行中的订单。
--
--    这是需求6「只有车辆处于可运营状态时才能接单」的筛选层：
--    派单候选只从本视图里选，避免各处重复写 EXISTS 子查询。
--
--    「没有进行中的订单」这一条是必需的：一个正在载客的司机不是有效运力。
--    若不加，平台会把订单派给正在跑单的司机，对方一点「接单」就会撞上
--    uk_active_driver 唯一索引被拒（DRIVER_BUSY），白白浪费一轮派单机会。
--    加上之后，DRIVER_BUSY 退化为纯粹的并发竞态兜底，而不再是常态。
-- -------------------------------------------------------------
CREATE VIEW `v_available_drivers` AS
SELECT
    d.`driver_id`,
    d.`real_name`,
    d.`phone`,
    d.`region`,
    d.`online_status`,
    d.`service_score`,
    d.`review_count`,
    (SELECT COUNT(*)
       FROM `vehicle` v
      WHERE v.`driver_id` = d.`driver_id`
        AND v.`operate_status` = 1) AS `available_vehicle_count`
FROM `driver` d
JOIN `account` a ON a.`account_id` = d.`account_id`
WHERE d.`online_status` = 1
  AND d.`status` = 1
  AND d.`audit_status` = 1
  AND a.`status` = 1
  AND EXISTS (SELECT 1
                FROM `vehicle` v
               WHERE v.`driver_id` = d.`driver_id`
                 AND v.`operate_status` = 1)
  AND NOT EXISTS (SELECT 1
                    FROM `ride_order` o
                   WHERE o.`driver_id` = d.`driver_id`
                     AND o.`status` IN ('accepted', 'arrived', 'in_progress'));


-- -------------------------------------------------------------
-- 3. v_order_detail —— 订单全息视图
--    订单 + 乘客 + 司机 + 车辆 + 车型，并把英文状态映射为中文。
--    三端的订单列表/详情页统一查本视图。
-- -------------------------------------------------------------
CREATE VIEW `v_order_detail` AS
SELECT
    o.`order_id`,
    o.`order_no`,
    o.`status`,
    CASE o.`status`
        WHEN 'pending'     THEN '待接单'
        WHEN 'accepted'    THEN '已接单'
        WHEN 'arrived'     THEN '司机已到达'
        WHEN 'in_progress' THEN '行程中'
        WHEN 'completed'   THEN '已完成'
        WHEN 'cancelled'   THEN '已取消'
        ELSE '未知'
    END AS `status_text`,

    o.`passenger_id`,
    p.`real_name`   AS `passenger_name`,
    p.`phone`       AS `passenger_phone`,

    o.`driver_id`,
    d.`real_name`   AS `driver_name`,
    d.`phone`       AS `driver_phone`,
    d.`service_score` AS `driver_service_score`,

    o.`vehicle_id`,
    v.`plate_no`,
    v.`brand`,
    v.`model`,
    v.`color`,

    o.`car_type_id`,
    ct.`type_name`  AS `car_type_name`,

    o.`origin_address`, o.`origin_lng`, o.`origin_lat`, o.`origin_region`,
    o.`dest_address`,   o.`dest_lng`,   o.`dest_lat`,

    o.`created_at`, o.`accepted_at`, o.`arrived_at`,
    o.`started_at`, o.`completed_at`, o.`cancelled_at`,
    o.`cancel_reason`, o.`cancelled_by`,

    o.`pricing_rule_id`, o.`rule_version`,
    o.`snap_base_price`, o.`snap_base_distance_km`,
    o.`snap_per_km_price`, o.`snap_per_min_price`, o.`snap_min_price`,
    o.`distance_km`, o.`duration_min`,
    o.`est_fare`, o.`fare_amount`,
    o.`passenger_remark`,
    o.`updated_at`
FROM `ride_order` o
JOIN      `passenger` p  ON p.`passenger_id` = o.`passenger_id`
JOIN      `car_type`  ct ON ct.`car_type_id` = o.`car_type_id`
LEFT JOIN `driver`    d  ON d.`driver_id`    = o.`driver_id`
LEFT JOIN `vehicle`   v  ON v.`vehicle_id`   = o.`vehicle_id`;


-- -------------------------------------------------------------
-- 4. v_order_status_duration —— 从状态日志计算各节点耗时
--    这是「状态日志表」相对「主表时间戳列」的附加价值：
--    可做时间维度分析（等待接单时长、接驾时长、行程时长）。
-- -------------------------------------------------------------
CREATE VIEW `v_order_status_duration` AS
SELECT
    o.`order_id`,
    o.`order_no`,
    o.`status` AS `current_status`,

    -- 各节点时间（取该状态首次到达的时间）
    MIN(CASE WHEN l.`to_status` = 'pending'     THEN l.`changed_at` END) AS `t_pending`,
    MIN(CASE WHEN l.`to_status` = 'accepted'    THEN l.`changed_at` END) AS `t_accepted`,
    MIN(CASE WHEN l.`to_status` = 'arrived'     THEN l.`changed_at` END) AS `t_arrived`,
    MIN(CASE WHEN l.`to_status` = 'in_progress' THEN l.`changed_at` END) AS `t_in_progress`,
    MIN(CASE WHEN l.`to_status` = 'completed'   THEN l.`changed_at` END) AS `t_completed`,

    -- 阶段耗时（秒）
    TIMESTAMPDIFF(SECOND,
        MIN(CASE WHEN l.`to_status` = 'pending'  THEN l.`changed_at` END),
        MIN(CASE WHEN l.`to_status` = 'accepted' THEN l.`changed_at` END)) AS `wait_accept_sec`,
    TIMESTAMPDIFF(SECOND,
        MIN(CASE WHEN l.`to_status` = 'accepted' THEN l.`changed_at` END),
        MIN(CASE WHEN l.`to_status` = 'arrived'  THEN l.`changed_at` END)) AS `pickup_sec`,
    TIMESTAMPDIFF(SECOND,
        MIN(CASE WHEN l.`to_status` = 'in_progress' THEN l.`changed_at` END),
        MIN(CASE WHEN l.`to_status` = 'completed'   THEN l.`changed_at` END)) AS `trip_sec`,
    TIMESTAMPDIFF(SECOND,
        MIN(CASE WHEN l.`to_status` = 'pending'   THEN l.`changed_at` END),
        MIN(CASE WHEN l.`to_status` = 'completed' THEN l.`changed_at` END)) AS `total_sec`
FROM `ride_order` o
JOIN `ride_order_status_log` l ON l.`order_id` = o.`order_id`
GROUP BY o.`order_id`, o.`order_no`, o.`status`;


-- -------------------------------------------------------------
-- 5. v_driver_service_score —— 司机服务分聚合
--    与 driver.service_score（由触发器维护）互为校验：
--    报告里可对比两者是否一致，说明「触发器维护的冗余值」正确性。
-- -------------------------------------------------------------
CREATE VIEW `v_driver_service_score` AS
SELECT
    d.`driver_id`,
    d.`real_name`,
    d.`region`,
    d.`service_score`                        AS `stored_score`,
    d.`review_count`                         AS `stored_count`,
    COUNT(r.`review_id`)                     AS `actual_count`,
    ROUND(AVG(r.`score`), 2)                 AS `actual_avg_score`,
    SUM(CASE WHEN r.`score` >= 4 THEN 1 ELSE 0 END) AS `good_review_count`,
    SUM(CASE WHEN r.`score` <= 2 THEN 1 ELSE 0 END) AS `bad_review_count`
FROM `driver` d
LEFT JOIN `order_review` r ON r.`driver_id` = d.`driver_id`
GROUP BY d.`driver_id`, d.`real_name`, d.`region`, d.`service_score`, d.`review_count`;


-- -------------------------------------------------------------
-- 6. v_expiring_licenses —— 证照/保险/年检到期提醒（选做）
--    UNION 三种证照，统一成「主体 + 证照类型 + 到期日 + 剩余天数」。
-- -------------------------------------------------------------
CREATE VIEW `v_expiring_licenses` AS
SELECT
    'driver'      AS `subject_type`,
    d.`driver_id` AS `subject_id`,
    d.`real_name` AS `subject_name`,
    '驾驶证'      AS `license_type`,
    d.`driver_license_no` AS `license_no`,
    d.`license_expire_date` AS `expire_date`,
    DATEDIFF(d.`license_expire_date`, CURDATE()) AS `days_left`
FROM `driver` d
WHERE d.`license_expire_date` IS NOT NULL
  AND d.`status` = 1

UNION ALL

SELECT
    'vehicle', v.`vehicle_id`, v.`plate_no`,
    '交强险', v.`vehicle_license_no`,
    v.`insurance_expire_date`,
    DATEDIFF(v.`insurance_expire_date`, CURDATE())
FROM `vehicle` v
WHERE v.`insurance_expire_date` IS NOT NULL
  AND v.`operate_status` <> 2

UNION ALL

SELECT
    'vehicle', v.`vehicle_id`, v.`plate_no`,
    '年检', v.`vehicle_license_no`,
    v.`annual_check_expire_date`,
    DATEDIFF(v.`annual_check_expire_date`, CURDATE())
FROM `vehicle` v
WHERE v.`annual_check_expire_date` IS NOT NULL
  AND v.`operate_status` <> 2;


-- -------------------------------------------------------------
-- 验证：列出已建立的视图
-- -------------------------------------------------------------
SELECT TABLE_NAME AS `视图名`
FROM information_schema.VIEWS
WHERE TABLE_SCHEMA = 'taxi_system'
ORDER BY TABLE_NAME;
