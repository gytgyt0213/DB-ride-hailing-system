-- =============================================================
-- 05_seed_base.sql
-- 网约车（打车）系统 —— 基础数据（字典、管理员、首批计价规则）
--
-- 执行方式： mysql -u root -p --default-character-set=utf8mb4 < sql/05_seed_base.sql
-- 前置条件：已执行 00、01（本脚本不依赖 02/03/04，但建议按序执行）
--
-- 演示账号： admin / admin123
--   密码哈希 = PBKDF2-HMAC-SHA256(密码, 盐, 200000 次迭代)
--   盐值与迭代次数单独存列，便于日后升级算法而不影响历史账号
-- =============================================================

USE `taxi_system`;
SET NAMES utf8mb4;


-- -------------------------------------------------------------
-- 1. 角色字典
-- -------------------------------------------------------------
INSERT INTO `role` (`role_code`, `role_name`, `description`) VALUES
    ('admin',     '平台管理员', '维护平台基础数据、管理司机与车辆账号、处理异常订单'),
    ('driver',    '司机',       '注册登录、上线接单、执行行程、结束行程'),
    ('passenger', '乘客',       '注册登录、发起叫车、查看订单状态');


-- -------------------------------------------------------------
-- 2. 车型字典（显式指定 ID，保证后续外键引用稳定）
-- -------------------------------------------------------------
INSERT INTO `car_type` (`car_type_id`, `type_code`, `type_name`, `seat_count`, `status`) VALUES
    (1, 'economy',  '经济型', 4, 1),
    (2, 'comfort',  '舒适型', 4, 1),
    (3, 'business', '商务型', 6, 1),
    (4, 'luxury',   '豪华型', 4, 1);


-- -------------------------------------------------------------
-- 3. 平台管理员账号
--    用户名 admin，密码 admin123
-- -------------------------------------------------------------
INSERT INTO `account`
    (`username`, `password_hash`, `salt`, `pwd_iterations`, `pwd_algo`, `role_code`, `status`)
VALUES
    ('admin',
     '91470b2d6b36748444dc4a2f5fc5aaed1747b7b37f55bfb48d791fd4e1e5e475',
     '49883ce1174697272b55c2d60656df03',
     200000,
     'pbkdf2_sha256',
     'admin',
     1);

SET @admin_account_id = LAST_INSERT_ID();

INSERT INTO `admin` (`account_id`, `real_name`, `phone`) VALUES
    (@admin_account_id, '系统管理员', '13800000000');

-- 第 2 个管理员：演示「多管理员」与 pricing_rule.created_by 溯源
INSERT INTO `account`
    (`username`, `password_hash`, `salt`, `pwd_iterations`, `pwd_algo`, `role_code`, `status`)
VALUES
    ('admin2',
     '91470b2d6b36748444dc4a2f5fc5aaed1747b7b37f55bfb48d791fd4e1e5e475',
     '49883ce1174697272b55c2d60656df03',
     200000,
     'pbkdf2_sha256',
     'admin',
     1);

SET @admin2_account_id = LAST_INSERT_ID();

INSERT INTO `admin` (`account_id`, `real_name`, `phone`) VALUES
    (@admin2_account_id, '运营管理员', '13800000001');

SET @admin_id = (SELECT `admin_id` FROM `admin` WHERE `account_id` = @admin_account_id);


-- -------------------------------------------------------------
-- 4. 各车型首批计价规则（版本 1，长期有效）
--
--    版本化约定：改价时不 UPDATE 已有行的单价列，
--    而是「插入新版本行 + 把旧行的 effective_to 设为新行生效时刻」。
--    详见 docs/数据库设计说明.md。
--
--    计费公式（见 fn_calc_fare）：
--      车费 = 起步价
--           + MAX(0, 里程 - 起步含里程) * 里程单价
--           + 时长 * 时长单价
--      车费 = MAX(车费, 最低消费)
-- -------------------------------------------------------------
INSERT INTO `pricing_rule`
    (`car_type_id`, `rule_name`, `version`,
     `base_price`, `base_distance_km`, `per_km_price`, `per_min_price`, `min_price`,
     `effective_from`, `effective_to`, `status`, `created_by`)
VALUES
    (1, '经济型-2026标准版', 1, 10.00, 3.00, 2.30, 0.40, 12.00, '2026-01-01 00:00:00', NULL, 1, @admin_id),
    (2, '舒适型-2026标准版', 1, 14.00, 3.00, 2.80, 0.50, 16.00, '2026-01-01 00:00:00', NULL, 1, @admin_id),
    (3, '商务型-2026标准版', 1, 20.00, 3.00, 3.50, 0.70, 25.00, '2026-01-01 00:00:00', NULL, 1, @admin_id),
    (4, '豪华型-2026标准版', 1, 30.00, 3.00, 5.00, 1.00, 40.00, '2026-01-01 00:00:00', NULL, 1, @admin_id);


-- -------------------------------------------------------------
-- 5. 验证
-- -------------------------------------------------------------
SELECT '角色' AS `字典`, COUNT(*) AS `条数` FROM `role`
UNION ALL SELECT '车型', COUNT(*) FROM `car_type`
UNION ALL SELECT '管理员', COUNT(*) FROM `admin`
UNION ALL SELECT '计价规则', COUNT(*) FROM `pricing_rule`;

SELECT `car_type_id` AS `车型`, `rule_name` AS `规则`,
       `version` AS `版本`, `base_price` AS `起步价`,
       `per_km_price` AS `里程单价`, `per_min_price` AS `时长单价`,
       `min_price` AS `最低消费`
FROM `v_pricing_current`
ORDER BY `car_type_id`;
