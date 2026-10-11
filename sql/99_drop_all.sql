-- =============================================================
-- 99_drop_all.sql
-- 网约车（打车）系统 —— 全部对象清理（用于重置）
--
-- 执行方式： mysql -u root -p < sql/99_drop_all.sql
--
-- ⚠️ 危险操作：会删除 taxi_system 库中的全部对象与数据。
--    仅用于开发期重置，重置后请按顺序重新执行：
--        00 -> 01 -> 02 -> 03 -> 04 -> 05 -> 06 -> 07
--
--    本脚本只操作 taxi_system 一个库，
--    不会影响机器上其它数据库（bookstore / online_mall / student_2026 / tpch 等）。
-- =============================================================

USE `taxi_system`;
SET NAMES utf8mb4;

-- 触发器 / 存储过程 / 函数
DROP TRIGGER  IF EXISTS `trg_review_after_insert`;
DROP PROCEDURE IF EXISTS `sp_create_order`;
DROP PROCEDURE IF EXISTS `sp_dispatch_order`;
DROP PROCEDURE IF EXISTS `sp_accept_order`;
DROP PROCEDURE IF EXISTS `sp_complete_order`;
DROP PROCEDURE IF EXISTS `sp_cancel_order`;
DROP PROCEDURE IF EXISTS `sp_expire_dispatch`;
DROP FUNCTION  IF EXISTS `fn_calc_fare`;

-- 定时事件（选做：证照到期提醒 / 派单过期清理）
DROP EVENT IF EXISTS `ev_expire_dispatch`;
DROP EVENT IF EXISTS `ev_license_alert`;

-- 视图
DROP VIEW IF EXISTS `v_expiring_licenses`;
DROP VIEW IF EXISTS `v_driver_service_score`;
DROP VIEW IF EXISTS `v_order_status_duration`;
DROP VIEW IF EXISTS `v_order_detail`;
DROP VIEW IF EXISTS `v_available_drivers`;
DROP VIEW IF EXISTS `v_pricing_current`;

-- 表（子表 -> 父表，避免外键阻塞）
DROP TABLE IF EXISTS `order_review`;
DROP TABLE IF EXISTS `order_track`;
DROP TABLE IF EXISTS `vehicle_location_current`;
DROP TABLE IF EXISTS `order_dispatch`;
DROP TABLE IF EXISTS `ride_order_status_log`;
DROP TABLE IF EXISTS `ride_order`;
DROP TABLE IF EXISTS `pricing_rule`;
DROP TABLE IF EXISTS `common_address`;
DROP TABLE IF EXISTS `vehicle`;
DROP TABLE IF EXISTS `user_session`;
DROP TABLE IF EXISTS `driver`;
DROP TABLE IF EXISTS `passenger`;
DROP TABLE IF EXISTS `admin`;
DROP TABLE IF EXISTS `account`;
DROP TABLE IF EXISTS `car_type`;
DROP TABLE IF EXISTS `role`;

SELECT 'taxi_system 已清空' AS `状态`;
