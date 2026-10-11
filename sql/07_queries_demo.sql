-- =============================================================
-- 07_queries_demo.sql
-- 网约车（打车）系统 —— 演示查询集（实验报告用）
--
-- 执行方式： mysql -u root -p --default-character-set=utf8mb4 < sql/07_queries_demo.sql
-- 前置条件：已执行 00 ~ 06
--
-- 本文件集中展示系统用到的典型 SQL，也是实验报告「查询实现」章节的素材：
--   一、基础统计      二、多表连接与视图      三、聚合与分组统计
--   四、时间维度分析  五、计价核对            六、触发器一致性校验
--   七、选做功能查询  八、索引优化与 EXPLAIN  九、事务与并发验证
-- =============================================================

USE `taxi_system`;
SET NAMES utf8mb4;


-- =============================================================
-- 一、基础统计
-- =============================================================

SELECT '===== 1.1 订单状态分布 =====' AS `---`;
SELECT
    `status`                                            AS `状态码`,
    CASE `status`
        WHEN 'pending'     THEN '待接单'
        WHEN 'accepted'    THEN '已接单'
        WHEN 'arrived'     THEN '司机已到达'
        WHEN 'in_progress' THEN '行程中'
        WHEN 'completed'   THEN '已完成'
        WHEN 'cancelled'   THEN '已取消'
    END                                                 AS `状态`,
    COUNT(*)                                            AS `订单数`,
    CONCAT(ROUND(COUNT(*) * 100.0 /
           (SELECT COUNT(*) FROM `ride_order`), 2), '%') AS `占比`
FROM `ride_order`
GROUP BY `status`
ORDER BY `订单数` DESC;


SELECT '===== 1.2 各车型订单量与收入 =====' AS `---`;
SELECT
    ct.`type_name`                                  AS `车型`,
    COUNT(o.`order_id`)                             AS `订单数`,
    SUM(CASE WHEN o.`status` = 'completed' THEN 1 ELSE 0 END) AS `完成数`,
    ROUND(SUM(IFNULL(o.`fare_amount`, 0)), 2)       AS `总营收`,
    ROUND(AVG(NULLIF(o.`fare_amount`, 0)), 2)       AS `平均车费`
FROM `car_type` ct
LEFT JOIN `ride_order` o ON o.`car_type_id` = ct.`car_type_id`
GROUP BY ct.`car_type_id`, ct.`type_name`
ORDER BY `订单数` DESC;


-- =============================================================
-- 二、多表连接与视图
-- =============================================================

SELECT '===== 2.1 订单详情（走 v_order_detail 视图，自动带中文状态）=====' AS `---`;
SELECT `order_id`, `order_no`, `status_text`, `passenger_name`,
       `driver_name`, `plate_no`, `car_type_name`,
       `origin_address`, `dest_address`, `fare_amount`
FROM `v_order_detail`
WHERE `status` = 'completed'
ORDER BY `completed_at` DESC
LIMIT 10;


SELECT '===== 2.2 手动多表连接（等价于视图内部实现，用于对比）=====' AS `---`;
SELECT
    o.`order_no`,
    p.`real_name`  AS `乘客`,
    d.`real_name`  AS `司机`,
    v.`plate_no`   AS `车牌`,
    ct.`type_name` AS `车型`,
    o.`fare_amount` AS `车费`
FROM `ride_order` o
JOIN `passenger` p  ON p.`passenger_id` = o.`passenger_id`
JOIN `car_type`  ct ON ct.`car_type_id` = o.`car_type_id`
LEFT JOIN `driver`  d ON d.`driver_id`  = o.`driver_id`
LEFT JOIN `vehicle` v ON v.`vehicle_id` = o.`vehicle_id`
WHERE o.`status` = 'completed'
ORDER BY o.`completed_at` DESC
LIMIT 10;


SELECT '===== 2.3 当前可接单司机（v_available_drivers）=====' AS `---`;
SELECT `driver_id`, `real_name`, `region`, `service_score`, `available_vehicle_count`
FROM `v_available_drivers`
ORDER BY `service_score` DESC, `driver_id`
LIMIT 10;


-- =============================================================
-- 三、聚合与分组统计
-- =============================================================

SELECT '===== 3.1 司机接单排行 TOP 10 =====' AS `---`;
SELECT
    d.`driver_id`,
    d.`real_name`                          AS `司机`,
    d.`region`                             AS `区域`,
    COUNT(o.`order_id`)                    AS `完成单数`,
    ROUND(SUM(o.`fare_amount`), 2)         AS `营收合计`,
    ROUND(AVG(o.`fare_amount`), 2)         AS `平均单价`,
    ROUND(AVG(o.`distance_km`), 2)         AS `平均里程`
FROM `driver` d
JOIN `ride_order` o ON o.`driver_id` = d.`driver_id` AND o.`status` = 'completed'
GROUP BY d.`driver_id`, d.`real_name`, d.`region`
ORDER BY `完成单数` DESC, `营收合计` DESC
LIMIT 10;


SELECT '===== 3.2 区域订单量（选做：区域供需统计的基础）=====' AS `---`;
SELECT
    o.`origin_region`                      AS `上车区域`,
    COUNT(*)                               AS `订单总量`,
    SUM(CASE WHEN o.`status` = 'completed' THEN 1 ELSE 0 END) AS `完成`,
    SUM(CASE WHEN o.`status` = 'cancelled' THEN 1 ELSE 0 END) AS `取消`,
    ROUND(SUM(CASE WHEN o.`status` = 'cancelled' THEN 1 ELSE 0 END)
          * 100.0 / COUNT(*), 2)           AS `取消率%`,
    ROUND(AVG(NULLIF(o.`distance_km`, 0)), 2) AS `平均里程`,
    (SELECT COUNT(*) FROM `v_available_drivers` ad
      WHERE ad.`region` = o.`origin_region`)  AS `当前在线可用司机`
FROM `ride_order` o
GROUP BY o.`origin_region`
ORDER BY `订单总量` DESC;


SELECT '===== 3.3 区域供需比（选做：热力图数据）=====' AS `---`;
-- 需求侧看最近 7 天订单量，供给侧看在线的可接单司机数
SELECT
    r.`region`                                  AS `区域`,
    IFNULL(o.`recent_orders`, 0)                AS `近7天订单`,
    IFNULL(s.`online_drivers`, 0)               AS `在线司机`,
    CASE
        WHEN IFNULL(s.`online_drivers`, 0) = 0 THEN NULL
        ELSE ROUND(IFNULL(o.`recent_orders`, 0) / s.`online_drivers`, 2)
    END                                         AS `供需比(单/司机)`
FROM (SELECT DISTINCT `region` FROM `driver`) r
LEFT JOIN (
    SELECT `origin_region` AS `region`, COUNT(*) AS `recent_orders`
    FROM `ride_order`
    WHERE `created_at` >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    GROUP BY `origin_region`
) o ON o.`region` = r.`region`
LEFT JOIN (
    SELECT `region`, COUNT(*) AS `online_drivers`
    FROM `v_available_drivers`
    GROUP BY `region`
) s ON s.`region` = r.`region`
ORDER BY `近7天订单` DESC;


-- =============================================================
-- 四、时间维度分析（体现「状态日志表」的价值）
-- =============================================================

SELECT '===== 4.1 各阶段平均耗时（来自 v_order_status_duration）=====' AS `---`;
SELECT
    COUNT(*)                                        AS `样本订单数`,
    ROUND(AVG(`wait_accept_sec`), 1)                AS `平均等待接单_秒`,
    ROUND(AVG(`pickup_sec`), 1)                     AS `平均接驾_秒`,
    ROUND(AVG(`trip_sec`), 1)                       AS `平均行程_秒`,
    ROUND(AVG(`total_sec`), 1)                      AS `平均总耗时_秒`,
    ROUND(MAX(`wait_accept_sec`), 1)                AS `最长等待_秒`
FROM `v_order_status_duration`
WHERE `t_completed` IS NOT NULL;


SELECT '===== 4.2 按小时统计订单量（高峰时段分析）=====' AS `---`;
SELECT
    HOUR(`created_at`)                              AS `小时`,
    COUNT(*)                                        AS `订单数`,
    LPAD('', ROUND(COUNT(*) / 8), '*')              AS `分布图`
FROM `ride_order`
GROUP BY HOUR(`created_at`)
ORDER BY `小时`;


SELECT '===== 4.3 按月统计订单量与营收 =====' AS `---`;
SELECT
    DATE_FORMAT(`created_at`, '%Y-%m')              AS `月份`,
    COUNT(*)                                        AS `订单数`,
    SUM(CASE WHEN `status` = 'completed' THEN 1 ELSE 0 END) AS `完成数`,
    ROUND(SUM(IFNULL(`fare_amount`, 0)), 2)         AS `营收`,
    ROUND(AVG(NULLIF(`fare_amount`, 0)), 2)         AS `客单价`
FROM `ride_order`
GROUP BY DATE_FORMAT(`created_at`, '%Y-%m')
ORDER BY `月份`;


SELECT '===== 4.4 从状态日志反查某一单的完整时间线 =====' AS `---`;
SELECT
    l.`log_id`,
    l.`from_status`                                 AS `原状态`,
    l.`to_status`                                   AS `新状态`,
    l.`operator_type`                               AS `操作者类型`,
    l.`operator_id`                                 AS `操作者ID`,
    l.`changed_at`                                  AS `变更时间`,
    ROUND(TIMESTAMPDIFF(SECOND,
        LAG(l.`changed_at`) OVER (ORDER BY l.`log_id`),
        l.`changed_at`), 1)                         AS `距上一步_秒`,
    l.`remark`                                      AS `备注`
FROM `ride_order_status_log` l
WHERE l.`order_id` = (SELECT `order_id` FROM `ride_order`
                       WHERE `status` = 'completed' ORDER BY `order_id` LIMIT 1)
ORDER BY l.`log_id`;


-- =============================================================
-- 五、计价核对（验证「价格快照 + 存储函数」的一致性）
-- =============================================================

SELECT '===== 5.1 快照计价 vs 用当前规则重算（应完全一致，证明快照有效）=====' AS `---`;
SELECT
    o.`order_no`,
    o.`rule_version`                                AS `快照版本`,
    o.`distance_km`                                 AS `里程`,
    o.`duration_min`                                AS `时长`,
    o.`fare_amount`                                 AS `订单车费`,
    `fn_calc_fare`(
        o.`snap_base_price`, o.`snap_base_distance_km`,
        o.`snap_per_km_price`, o.`snap_per_min_price`,
        o.`snap_min_price`, o.`distance_km`, o.`duration_min`
    )                                               AS `按快照重算`,
    CASE WHEN o.`fare_amount` = `fn_calc_fare`(
            o.`snap_base_price`, o.`snap_base_distance_km`,
            o.`snap_per_km_price`, o.`snap_per_min_price`,
            o.`snap_min_price`, o.`distance_km`, o.`duration_min`)
         THEN '一致' ELSE '不一致' END             AS `核对结果`
FROM `ride_order` o
WHERE o.`status` = 'completed'
ORDER BY o.`order_id`
LIMIT 15;


SELECT '===== 5.2 计价公式展开核对（手工公式 vs 存储函数）=====' AS `---`;
SELECT
    o.`order_no`,
    o.`snap_base_price`                             AS `起步价`,
    ROUND(GREATEST(0, o.`distance_km` - o.`snap_base_distance_km`)
          * o.`snap_per_km_price`, 2)               AS `超出里程费`,
    ROUND(o.`duration_min` * o.`snap_per_min_price`, 2) AS `时长费`,
    ROUND(GREATEST(
            o.`snap_base_price`
            + GREATEST(0, o.`distance_km` - o.`snap_base_distance_km`) * o.`snap_per_km_price`
            + o.`duration_min` * o.`snap_per_min_price`,
            o.`snap_min_price`), 2)                 AS `手工计算`,
    `fn_calc_fare`(
        o.`snap_base_price`, o.`snap_base_distance_km`,
        o.`snap_per_km_price`, o.`snap_per_min_price`,
        o.`snap_min_price`, o.`distance_km`, o.`duration_min`
    )                                               AS `函数结果`
FROM `ride_order` o
WHERE o.`status` = 'completed'
ORDER BY o.`order_id`
LIMIT 10;


-- =============================================================
-- 六、触发器一致性校验
-- =============================================================

SELECT '===== 6.1 司机服务分：触发器维护值 vs 实时聚合值（应一致）=====' AS `---`;
SELECT
    `driver_id`, `real_name`                        AS `司机`,
    `stored_score`                                  AS `触发器维护分`,
    `actual_avg_score`                              AS `实时聚合分`,
    `stored_count`                                  AS `触发器维护数`,
    `actual_count`                                  AS `实际评价数`,
    CASE WHEN `stored_count` = `actual_count`
              AND ABS(IFNULL(`stored_score`, 0) - IFNULL(`actual_avg_score`, 0)) < 0.011
         THEN '一致' ELSE '不一致' END             AS `校验`
FROM `v_driver_service_score`
WHERE `actual_count` > 0
ORDER BY `actual_count` DESC
LIMIT 15;


SELECT '===== 6.2 服务分排行（选做：司机服务分）=====' AS `---`;
SELECT
    `driver_id`, `real_name`                        AS `司机`,
    `region`                                        AS `区域`,
    `stored_score`                                  AS `服务分`,
    `actual_count`                                  AS `评价数`,
    `good_review_count`                             AS `好评数(4-5分)`,
    `bad_review_count`                              AS `差评数(1-2分)`,
    ROUND(`good_review_count` * 100.0 / NULLIF(`actual_count`, 0), 1) AS `好评率%`
FROM `v_driver_service_score`
WHERE `actual_count` >= 3
ORDER BY `服务分` DESC, `评价数` DESC
LIMIT 10;


-- =============================================================
-- 七、选做功能查询
-- =============================================================

SELECT '===== 7.1 证照/保险/年检 30 天内到期清单（选做：到期提醒）=====' AS `---`;
SELECT
    `subject_type`                                  AS `主体类型`,
    `subject_id`                                    AS `主体ID`,
    `subject_name`                                  AS `名称`,
    `license_type`                                  AS `证照类型`,
    `expire_date`                                   AS `到期日`,
    `days_left`                                     AS `剩余天数`,
    CASE
        WHEN `days_left` < 0  THEN '已过期'
        WHEN `days_left` <= 7 THEN '紧急'
        ELSE '即将到期'
    END                                             AS `紧急程度`
FROM `v_expiring_licenses`
WHERE `days_left` <= 30
ORDER BY `days_left`;


SELECT '===== 7.2 未通过审核 / 被驳回的司机（选做：资质审核）=====' AS `---`;
SELECT
    d.`driver_id`,
    d.`real_name`                                   AS `司机`,
    d.`phone`                                       AS `手机`,
    CASE d.`audit_status` WHEN 0 THEN '待审核' WHEN 2 THEN '已驳回' END AS `审核状态`,
    d.`created_at`                                  AS `注册时间`,
    (SELECT COUNT(*) FROM `vehicle` v WHERE v.`driver_id` = d.`driver_id`) AS `名下车辆数`
FROM `driver` d
WHERE d.`audit_status` <> 1
ORDER BY d.`created_at`;


SELECT '===== 7.3 某订单的轨迹点（选做：轨迹回放数据源）=====' AS `---`;
SELECT
    t.`track_id`,
    t.`longitude`                                   AS `经度`,
    t.`latitude`                                    AS `纬度`,
    t.`speed`                                       AS `速度km/h`,
    t.`located_at`                                  AS `采样时间`
FROM `order_track` t
WHERE t.`order_id` = (SELECT `order_id` FROM `order_track`
                       GROUP BY `order_id` HAVING COUNT(*) >= 20
                       ORDER BY `order_id` LIMIT 1)
ORDER BY t.`located_at`
LIMIT 25;


SELECT '===== 7.4 派单成功率与司机拒单统计 =====' AS `---`;
SELECT
    od.`status`                                     AS `邀请状态`,
    COUNT(*)                                        AS `数量`,
    CONCAT(ROUND(COUNT(*) * 100.0 /
           (SELECT COUNT(*) FROM `order_dispatch`), 2), '%') AS `占比`
FROM `order_dispatch` od
GROUP BY od.`status`
ORDER BY `数量` DESC;


SELECT '===== 7.5 拒单率最高的司机 TOP 5 =====' AS `---`;
SELECT
    d.`driver_id`,
    d.`real_name`                                   AS `司机`,
    COUNT(*)                                        AS `收到邀请`,
    SUM(CASE WHEN od.`status` = 'rejected' THEN 1 ELSE 0 END) AS `拒单数`,
    ROUND(SUM(CASE WHEN od.`status` = 'rejected' THEN 1 ELSE 0 END)
          * 100.0 / COUNT(*), 1)                    AS `拒单率%`
FROM `order_dispatch` od
JOIN `driver` d ON d.`driver_id` = od.`driver_id`
GROUP BY d.`driver_id`, d.`real_name`
HAVING `收到邀请` >= 20
ORDER BY `拒单率%` DESC
LIMIT 5;


-- =============================================================
-- 八、索引优化与 EXPLAIN
--
-- 【实验方法】
--   步骤一：只执行到 01_create_tables.sql（不加 02_indexes.sql 的二级索引），
--           跑下面的 EXPLAIN，记录 type / key / rows 三列；
--   步骤二：执行 02_indexes.sql，再次 EXPLAIN 同样的语句；
--   步骤三：对比三列的变化，即为索引带来的执行计划改善。
--
--   若要在已建索引的库上重做「优化前」的对照，
--   可临时删除索引（示例）：
--       ALTER TABLE `ride_order` DROP INDEX `idx_order_status_created`;
--   对照完成后再用 02_indexes.sql 中的语句加回来。
-- =============================================================

SELECT '===== 8.1 待接单池查询：应命中 idx_order_status_created =====' AS `---`;
EXPLAIN SELECT `order_id`, `order_no`, `origin_address`
FROM `ride_order`
WHERE `status` = 'pending'
ORDER BY `created_at` DESC
LIMIT 20;

SELECT '===== 8.2 按区域派单候选：应命中 idx_order_region_status_time =====' AS `---`;
EXPLAIN SELECT `order_id`, `passenger_id`
FROM `ride_order`
WHERE `origin_region` = '朝阳区' AND `status` = 'pending'
ORDER BY `created_at`
LIMIT 20;

SELECT '===== 8.3 司机在线资格筛选：应命中 idx_driver_region_online =====' AS `---`;
EXPLAIN SELECT `driver_id`, `real_name`
FROM `driver`
WHERE `region` = '朝阳区' AND `online_status` = 1;

SELECT '===== 8.4 取当前计价规则：应命中 idx_rule_type_effective =====' AS `---`;
EXPLAIN SELECT `rule_id`, `base_price`, `per_km_price`
FROM `pricing_rule`
WHERE `car_type_id` = 1 AND `status` = 1
  AND `effective_from` <= NOW()
  AND (`effective_to` IS NULL OR `effective_to` > NOW());

SELECT '===== 8.5 乘客订单列表：应命中 idx_order_passenger_status_time =====' AS `---`;
EXPLAIN SELECT `order_id`, `status`, `created_at`
FROM `ride_order`
WHERE `passenger_id` = 1 AND `status` = 'completed'
ORDER BY `created_at` DESC
LIMIT 10;


-- =============================================================
-- 九、事务与并发验证
-- =============================================================

SELECT '===== 9.1 数据一致性：订单状态与日志首末条必须吻合 =====' AS `---`;
SELECT
    COUNT(*)                                        AS `不一致订单数`
FROM `ride_order` o
JOIN (
    SELECT `order_id`,
           SUBSTRING_INDEX(GROUP_CONCAT(`to_status` ORDER BY `log_id` DESC), ',', 1) AS `last_status`
    FROM `ride_order_status_log`
    GROUP BY `order_id`
) l ON l.`order_id` = o.`order_id`
WHERE o.`status` <> l.`last_status`;


SELECT '===== 9.2 各状态中文映射与订单数（对照 ENUM 定义）=====' AS `---`;
SELECT
    COLUMN_TYPE                                     AS `status列定义`
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'taxi_system'
  AND TABLE_NAME = 'ride_order' AND COLUMN_NAME = 'status';


SELECT '===== 9.3 生成列唯一索引：确认「一司一单」在库层生效 =====' AS `---`;
SELECT
    INDEX_NAME                                      AS `索引名`,
    COLUMN_NAME                                     AS `列`,
    NON_UNIQUE                                      AS `非唯一`
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'taxi_system'
  AND TABLE_NAME = 'ride_order'
  AND INDEX_NAME = 'uk_active_driver';

-- 当前处于「进行中」状态的订单，其司机应当两两不同
SELECT
    `driver_id`                                     AS `司机`,
    COUNT(*)                                        AS `进行中订单数`
FROM `ride_order`
WHERE `status` IN ('accepted', 'arrived', 'in_progress')
GROUP BY `driver_id`
HAVING COUNT(*) > 1;
-- ↑ 期望：0 行。若有结果说明唯一索引失效。


-- =============================================================
-- 十、总体数据概览（报告开头用）
-- =============================================================
SELECT '===== 10. 数据规模总览 =====' AS `---`;
SELECT '乘客'     AS `实体`, COUNT(*) AS `数量` FROM `passenger`
UNION ALL SELECT '司机',       COUNT(*) FROM `driver`
UNION ALL SELECT '车辆',       COUNT(*) FROM `vehicle`
UNION ALL SELECT '常用地址',   COUNT(*) FROM `common_address`
UNION ALL SELECT '计价规则',   COUNT(*) FROM `pricing_rule`
UNION ALL SELECT '订单',       COUNT(*) FROM `ride_order`
UNION ALL SELECT '状态日志',   COUNT(*) FROM `ride_order_status_log`
UNION ALL SELECT '派单记录',   COUNT(*) FROM `order_dispatch`
UNION ALL SELECT '轨迹点',     COUNT(*) FROM `order_track`
UNION ALL SELECT '评价',       COUNT(*) FROM `order_review`
UNION ALL SELECT '车辆定位',   COUNT(*) FROM `vehicle_location_current`;
