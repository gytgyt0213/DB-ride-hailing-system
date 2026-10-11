-- =============================================================
-- 00_create_database.sql
-- 网约车（打车）系统 —— 建库
-- 执行方式：
--   mysql -u root -p < sql/00_create_database.sql
-- 说明：库名 taxi_system，字符集 utf8mb4，排序规则 utf8mb4_0900_ai_ci
--       与库、表、连接三处字符集保持一致，避免中文乱码
-- =============================================================

CREATE DATABASE IF NOT EXISTS `taxi_system`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `taxi_system`;

SET NAMES utf8mb4;
