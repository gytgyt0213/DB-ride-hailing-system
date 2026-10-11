-- =============================================================
-- 02_indexes.sql
-- 网约车（打车）系统 —— 二级索引
--
-- 执行方式： mysql -u root -p < sql/02_indexes.sql
-- 前置条件：已执行 00、01
--
-- ⚠️ 本脚本只能执行一次（ALTER TABLE ADD INDEX 不可重复）。
--    如需重来，请走完整重置流程：99_drop_all.sql -> 00 -> 01 -> 02 -> ...
--
-- 【为什么把索引单独拆出来】
--   这些索引是「性能关键索引」，与主键/唯一键/外键支撑索引分开管理，
--   目的是支持报告中的索引优化实验：
--     步骤一：只执行 00+01（仅有 PK/UK/FK 索引），跑 07_queries_demo.sql 并 EXPLAIN；
--     步骤二：执行本脚本，再次 EXPLAIN 同样的查询；
--     步骤三：对比 type / key / rows 三列的变化。
-- =============================================================

USE `taxi_system`;
SET NAMES utf8mb4;


-- -------------------------------------------------------------
-- 1. driver：派单候选的核心查询索引
--    查询场景：按「区域 + 在线状态」筛可派单司机
--    SELECT ... FROM driver WHERE region=? AND online_status=1
--    复合索引遵循最左前缀：region 等值 + online_status 等值
-- -------------------------------------------------------------
ALTER TABLE `driver`
    ADD INDEX `idx_driver_region_online` (`region`, `online_status`);


-- -------------------------------------------------------------
-- 2. vehicle：按「车型 + 运营状态」筛车
--    查询场景：需求6「只有车辆处于可运营状态时才能接单」
--    以及按车型统计可运营车辆数
-- -------------------------------------------------------------
ALTER TABLE `vehicle`
    ADD INDEX `idx_vehicle_type_status` (`car_type_id`, `operate_status`);


-- -------------------------------------------------------------
-- 3. pricing_rule：取「当前生效规则」的核心索引
--    查询场景：
--      SELECT * FROM pricing_rule
--       WHERE car_type_id=? AND status=1
--         AND effective_from <= NOW()
--         AND (effective_to IS NULL OR effective_to > NOW())
--    时间有效窗查询，effective_from 等值/范围 + effective_to 范围
-- -------------------------------------------------------------
ALTER TABLE `pricing_rule`
    ADD INDEX `idx_rule_type_effective` (`car_type_id`, `effective_from`, `effective_to`);


-- -------------------------------------------------------------
-- 4. ride_order：四类高频列表/派单查询
-- -------------------------------------------------------------

-- 4.1 乘客「我的订单」列表（可按状态筛选，按下单时间倒序）
ALTER TABLE `ride_order`
    ADD INDEX `idx_order_passenger_status_time` (`passenger_id`, `status`, `created_at`);

-- 4.2 司机「我的行程」列表
ALTER TABLE `ride_order`
    ADD INDEX `idx_order_driver_status_time` (`driver_id`, `status`, `created_at`);

-- 4.3 待接单池 / 超时未接单扫描（需求2 派单、需求4 状态追踪）
ALTER TABLE `ride_order`
    ADD INDEX `idx_order_status_created` (`status`, `created_at`);

-- 4.4 按区域派单候选 / 区域供需统计（需求2 + 选做报表）
ALTER TABLE `ride_order`
    ADD INDEX `idx_order_region_status_time` (`origin_region`, `status`, `created_at`);

-- 4.5 按车型统计订单量（选做报表）
ALTER TABLE `ride_order`
    ADD INDEX `idx_order_car_type` (`car_type_id`);

-- 4.6 管理员订单监控：按完成时间做时间范围统计
ALTER TABLE `ride_order`
    ADD INDEX `idx_order_completed_at` (`completed_at`);


-- -------------------------------------------------------------
-- 5. order_dispatch：司机侧「待处理邀请」列表
--    （uk_order_driver 与 idx_dispatch_driver_status_time 已内联在 01，
--      此处补充按邀请时间倒序的覆盖查询需求）
-- -------------------------------------------------------------
ALTER TABLE `order_dispatch`
    ADD INDEX `idx_dispatch_order_batch` (`order_id`, `batch_no`);


-- -------------------------------------------------------------
-- 6. order_review：司机服务分聚合（选做）
-- -------------------------------------------------------------
ALTER TABLE `order_review`
    ADD INDEX `idx_review_driver_score` (`driver_id`, `score`);


-- -------------------------------------------------------------
-- 7. 验证索引已建立
-- -------------------------------------------------------------
SELECT
    TABLE_NAME       AS `表名`,
    INDEX_NAME       AS `索引名`,
    GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS `列(按序)`,
    NON_UNIQUE       AS `是否非唯一`
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = 'taxi_system'
  AND INDEX_NAME LIKE 'idx_%'
GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
ORDER BY TABLE_NAME, INDEX_NAME;
