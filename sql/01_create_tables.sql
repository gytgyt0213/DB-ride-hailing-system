-- =============================================================
-- 01_create_tables.sql
-- 网约车（打车）系统 —— 全部表结构（DDL）
--
-- 执行方式： mysql -u root -p < sql/01_create_tables.sql
--
-- 设计约定：
--   * 引擎 InnoDB，字符集 utf8mb4 / utf8mb4_0900_ai_ci
--   * 主键统一 BIGINT UNSIGNED AUTO_INCREMENT（字典表除外）
--   * 金额一律 DECIMAL(10,2)，绝不用 FLOAT/DOUBLE
--   * 经纬度 DECIMAL(10,6)；时间 DATETIME（轨迹表用 DATETIME(3) 毫秒精度）
--   * 所有标识符加反引号（order / rank 等为 MySQL 保留字，故订单表名为 ride_order）
--   * 建表顺序：父表 -> 子表（外键依赖）
--   * 外键默认 ON DELETE RESTRICT（防误删历史数据）
--
-- 运行前提：本机 sql_mode 含 ONLY_FULL_GROUP_BY 与 STRICT_TRANS_TABLES
-- =============================================================

USE `taxi_system`;
SET NAMES utf8mb4;

-- 反向顺序清理，保证脚本可重复执行
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


-- =============================================================
-- 一、字典表
-- =============================================================

CREATE TABLE `role` (
  `role_code`   VARCHAR(20)  NOT NULL                COMMENT '角色代码：admin/driver/passenger',
  `role_name`   VARCHAR(50)  NOT NULL                COMMENT '角色中文名',
  `description` VARCHAR(200)     NULL                COMMENT '说明',
  PRIMARY KEY (`role_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='角色字典表';


CREATE TABLE `car_type` (
  `car_type_id` TINYINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '车型ID',
  `type_code`   VARCHAR(20)      NOT NULL                COMMENT '车型代码：economy/comfort/business/luxury',
  `type_name`   VARCHAR(50)      NOT NULL                COMMENT '车型中文名',
  `seat_count`  TINYINT          NOT NULL DEFAULT 4      COMMENT '标准座位数',
  `status`      TINYINT          NOT NULL DEFAULT 1      COMMENT '1启用 0停用',
  `created_at`  DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`car_type_id`),
  UNIQUE KEY `uk_car_type_code` (`type_code`),
  CONSTRAINT `ck_car_type_status` CHECK (`status` IN (0, 1)),
  CONSTRAINT `ck_car_type_seat`   CHECK (`seat_count` BETWEEN 1 AND 60)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='车型字典表';


-- =============================================================
-- 二、账号与角色（泛化/特化模型：统一凭证表 + 各角色档案表 1:1）
-- =============================================================

CREATE TABLE `account` (
  `account_id`     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '账号ID',
  `username`       VARCHAR(50)     NOT NULL                COMMENT '登录名',
  `password_hash`  CHAR(64)        NOT NULL                COMMENT 'PBKDF2-HMAC-SHA256 哈希值（hex，32字节）',
  `salt`           CHAR(32)        NOT NULL                COMMENT '每用户随机盐（hex，16字节）',
  `pwd_iterations` INT UNSIGNED    NOT NULL DEFAULT 200000 COMMENT '迭代次数（随行存储，便于将来升级）',
  `pwd_algo`       VARCHAR(20)     NOT NULL DEFAULT 'pbkdf2_sha256' COMMENT '哈希算法标识',
  `role_code`      VARCHAR(20)     NOT NULL                COMMENT '所属角色',
  `status`         TINYINT         NOT NULL DEFAULT 1      COMMENT '1启用 0禁用',
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`account_id`),
  UNIQUE KEY `uk_account_username` (`username`),
  KEY `idx_account_role` (`role_code`),
  CONSTRAINT `ck_account_status` CHECK (`status` IN (0, 1)),
  CONSTRAINT `fk_account_role` FOREIGN KEY (`role_code`) REFERENCES `role` (`role_code`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='账号表（管理员/司机/乘客共用登录凭证）';


CREATE TABLE `admin` (
  `admin_id`   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '管理员ID',
  `account_id` BIGINT UNSIGNED NOT NULL                COMMENT '关联账号（1:1）',
  `real_name`  VARCHAR(50)     NOT NULL                COMMENT '姓名',
  `phone`      VARCHAR(20)         NULL                COMMENT '手机号',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`admin_id`),
  UNIQUE KEY `uk_admin_account` (`account_id`),
  CONSTRAINT `fk_admin_account` FOREIGN KEY (`account_id`) REFERENCES `account` (`account_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='管理员档案表';


CREATE TABLE `passenger` (
  `passenger_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '乘客ID',
  `account_id`   BIGINT UNSIGNED NOT NULL                COMMENT '关联账号（1:1）',
  `real_name`    VARCHAR(50)     NOT NULL                COMMENT '姓名',
  `phone`        VARCHAR(20)     NOT NULL                COMMENT '手机号',
  `gender`       TINYINT         NOT NULL DEFAULT 0      COMMENT '0未知 1男 2女',
  `home_region`  VARCHAR(50)         NULL                COMMENT '常驻区域',
  `status`       TINYINT         NOT NULL DEFAULT 1      COMMENT '1正常 0停用',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`passenger_id`),
  UNIQUE KEY `uk_passenger_account` (`account_id`),
  KEY `idx_passenger_phone` (`phone`),
  CONSTRAINT `ck_passenger_gender` CHECK (`gender` IN (0, 1, 2)),
  CONSTRAINT `ck_passenger_status` CHECK (`status` IN (0, 1)),
  CONSTRAINT `fk_passenger_account` FOREIGN KEY (`account_id`) REFERENCES `account` (`account_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='乘客档案表';


CREATE TABLE `driver` (
  `driver_id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '司机ID',
  `account_id`            BIGINT UNSIGNED NOT NULL                COMMENT '关联账号（1:1）',
  `real_name`             VARCHAR(50)     NOT NULL                COMMENT '姓名',
  `phone`                 VARCHAR(20)     NOT NULL                COMMENT '手机号',
  `id_card_no`            VARCHAR(18)         NULL                COMMENT '身份证号',
  `driver_license_no`     VARCHAR(30)         NULL                COMMENT '驾驶证号',
  `license_expire_date`   DATE                NULL                COMMENT '驾驶证到期日（选做：到期提醒）',
  `region`                VARCHAR(50)     NOT NULL DEFAULT 'default' COMMENT '所在区域（派单匹配用）',
  `online_status`         TINYINT         NOT NULL DEFAULT 0      COMMENT '0离线 1在线（可接单）',
  `audit_status`          TINYINT         NOT NULL DEFAULT 0      COMMENT '0待审核 1通过 2驳回（选做：证照审核）',
  `service_score`         DECIMAL(3,2)    NOT NULL DEFAULT 5.00   COMMENT '服务分（由评价触发维护）',
  `review_count`          INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '被评价次数',
  `status`                TINYINT         NOT NULL DEFAULT 1      COMMENT '1正常 0停用',
  `created_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`driver_id`),
  UNIQUE KEY `uk_driver_account` (`account_id`),
  UNIQUE KEY `uk_driver_id_card` (`id_card_no`),
  KEY `idx_driver_phone` (`phone`),
  CONSTRAINT `ck_driver_online`  CHECK (`online_status` IN (0, 1)),
  CONSTRAINT `ck_driver_audit`   CHECK (`audit_status` IN (0, 1, 2)),
  CONSTRAINT `ck_driver_status`  CHECK (`status` IN (0, 1)),
  CONSTRAINT `ck_driver_score`   CHECK (`service_score` >= 0 AND `service_score` <= 5),
  CONSTRAINT `fk_driver_account` FOREIGN KEY (`account_id`) REFERENCES `account` (`account_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='司机档案表';


CREATE TABLE `user_session` (
  `session_id` CHAR(64)        NOT NULL COMMENT '会话令牌（32字节随机数 hex）',
  `account_id` BIGINT UNSIGNED NOT NULL COMMENT '所属账号',
  `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `expires_at` DATETIME        NOT NULL COMMENT '过期时间',
  `ip`         VARCHAR(45)         NULL COMMENT '登录IP（兼容 IPv6）',
  `user_agent` VARCHAR(200)        NULL COMMENT '浏览器 UA',
  PRIMARY KEY (`session_id`),
  KEY `idx_session_account` (`account_id`),
  KEY `idx_session_expires` (`expires_at`),
  CONSTRAINT `fk_session_account` FOREIGN KEY (`account_id`) REFERENCES `account` (`account_id`)
      ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='服务端会话表';


-- =============================================================
-- 三、主体资源
-- =============================================================

CREATE TABLE `vehicle` (
  `vehicle_id`                BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT COMMENT '车辆ID',
  `driver_id`                 BIGINT UNSIGNED  NOT NULL                COMMENT '所属司机',
  `car_type_id`               TINYINT UNSIGNED NOT NULL                COMMENT '车型',
  `plate_no`                  VARCHAR(16)      NOT NULL                COMMENT '车牌号',
  `brand`                     VARCHAR(50)          NULL                COMMENT '品牌',
  `model`                     VARCHAR(50)          NULL                COMMENT '型号',
  `color`                     VARCHAR(20)          NULL                COMMENT '颜色',
  `seat_count`                TINYINT          NOT NULL                COMMENT '座位数',
  `operate_status`            TINYINT          NOT NULL DEFAULT 1      COMMENT '0不可运营(维修/停运) 1可运营 2已注销',
  `vehicle_license_no`        VARCHAR(30)          NULL                COMMENT '行驶证号',
  `register_date`             DATE                 NULL                COMMENT '注册日期',
  `insurance_expire_date`     DATE                 NULL                COMMENT '保险到期日（选做：到期提醒）',
  `annual_check_expire_date`  DATE                 NULL                COMMENT '年检到期日（选做：到期提醒）',
  `created_at`                DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`                DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`vehicle_id`),
  UNIQUE KEY `uk_vehicle_plate` (`plate_no`),
  KEY `idx_vehicle_driver` (`driver_id`),
  CONSTRAINT `ck_vehicle_operate` CHECK (`operate_status` IN (0, 1, 2)),
  CONSTRAINT `ck_vehicle_seat`    CHECK (`seat_count` BETWEEN 1 AND 60),
  CONSTRAINT `fk_vehicle_driver`    FOREIGN KEY (`driver_id`)    REFERENCES `driver` (`driver_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT `fk_vehicle_car_type`  FOREIGN KEY (`car_type_id`)  REFERENCES `car_type` (`car_type_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='车辆表';


CREATE TABLE `common_address` (
  `address_id`   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '地址ID',
  `passenger_id` BIGINT UNSIGNED NOT NULL                COMMENT '所属乘客',
  `label`        VARCHAR(20)     NOT NULL                COMMENT '标签：家/公司/学校等',
  `address_name` VARCHAR(200)    NOT NULL                COMMENT '地址文字描述',
  `longitude`    DECIMAL(10,6)       NULL                COMMENT '经度',
  `latitude`     DECIMAL(10,6)       NULL                COMMENT '纬度',
  `is_default`   TINYINT         NOT NULL DEFAULT 0      COMMENT '是否默认地址 1是 0否',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`address_id`),
  UNIQUE KEY `uk_address_passenger_label` (`passenger_id`, `label`),
  CONSTRAINT `ck_address_default` CHECK (`is_default` IN (0, 1)),
  CONSTRAINT `ck_address_lng` CHECK (`longitude` IS NULL OR (`longitude` BETWEEN -180 AND 180)),
  CONSTRAINT `ck_address_lat` CHECK (`latitude`  IS NULL OR (`latitude`  BETWEEN  -90 AND  90)),
  CONSTRAINT `fk_address_passenger` FOREIGN KEY (`passenger_id`) REFERENCES `passenger` (`passenger_id`)
      ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='乘客常用地址表';


-- =============================================================
-- 四、计价规则（车型差异化 + 版本化 + 时间有效窗）
--    改价 = 插新版本行 + 关闭旧版本行，永不变更已生效行的单价列
-- =============================================================

CREATE TABLE `pricing_rule` (
  `rule_id`           BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT COMMENT '规则ID',
  `car_type_id`       TINYINT UNSIGNED NOT NULL                COMMENT '适用车型',
  `rule_name`         VARCHAR(50)      NOT NULL                COMMENT '规则名称',
  `version`           INT UNSIGNED     NOT NULL DEFAULT 1      COMMENT '版本号（同车型内递增）',
  `base_price`        DECIMAL(10,2)    NOT NULL                COMMENT '起步价（元）',
  `base_distance_km`  DECIMAL(6,2)     NOT NULL DEFAULT 3.00   COMMENT '起步价包含里程（km）',
  `per_km_price`      DECIMAL(10,2)    NOT NULL                COMMENT '里程单价（元/km）',
  `per_min_price`     DECIMAL(10,2)    NOT NULL                COMMENT '时长单价（元/min）',
  `min_price`         DECIMAL(10,2)    NOT NULL DEFAULT 0.00   COMMENT '最低消费（元）',
  `effective_from`    DATETIME         NOT NULL                COMMENT '生效时间',
  `effective_to`      DATETIME             NULL                COMMENT '失效时间，NULL 表示当前有效',
  `status`            TINYINT          NOT NULL DEFAULT 1      COMMENT '1启用 0停用',
  `created_by`        BIGINT UNSIGNED      NULL                COMMENT '创建管理员',
  `created_at`        DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`        DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`rule_id`),
  UNIQUE KEY `uk_rule_type_version` (`car_type_id`, `version`),
  KEY `idx_rule_created_by` (`created_by`),
  CONSTRAINT `ck_rule_status`       CHECK (`status` IN (0, 1)),
  CONSTRAINT `ck_rule_base_price`   CHECK (`base_price`    >= 0),
  CONSTRAINT `ck_rule_base_km`      CHECK (`base_distance_km` >= 0),
  CONSTRAINT `ck_rule_per_km`       CHECK (`per_km_price`  >= 0),
  CONSTRAINT `ck_rule_per_min`      CHECK (`per_min_price` >= 0),
  CONSTRAINT `ck_rule_min_price`    CHECK (`min_price`     >= 0),
  CONSTRAINT `ck_rule_effective`    CHECK (`effective_to` IS NULL OR `effective_to` > `effective_from`),
  CONSTRAINT `fk_rule_car_type` FOREIGN KEY (`car_type_id`) REFERENCES `car_type` (`car_type_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT `fk_rule_admin`    FOREIGN KEY (`created_by`)  REFERENCES `admin` (`admin_id`)
      ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='计价规则表（版本化）';


-- =============================================================
-- 五、订单核心
-- =============================================================

CREATE TABLE `ride_order` (
  `order_id`          BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT COMMENT '订单ID',
  `order_no`          VARCHAR(32)      NOT NULL                COMMENT '业务订单号（唯一，便于展示）',
  `passenger_id`      BIGINT UNSIGNED  NOT NULL                COMMENT '乘客',
  `driver_id`         BIGINT UNSIGNED      NULL                COMMENT '接单司机（未接单为 NULL）',
  `vehicle_id`        BIGINT UNSIGNED      NULL                COMMENT '执行车辆（未接单为 NULL）',
  `car_type_id`       TINYINT UNSIGNED NOT NULL                COMMENT '乘客选择的车型',

  `origin_address`    VARCHAR(200)     NOT NULL                COMMENT '上车点文字描述',
  `origin_lng`        DECIMAL(10,6)        NULL                COMMENT '上车点经度',
  `origin_lat`        DECIMAL(10,6)        NULL                COMMENT '上车点纬度',
  `origin_region`     VARCHAR(50)      NOT NULL DEFAULT 'default' COMMENT '上车区域（派单/热力统计用）',
  `dest_address`      VARCHAR(200)     NOT NULL                COMMENT '下车点文字描述',
  `dest_lng`          DECIMAL(10,6)        NULL                COMMENT '下车点经度',
  `dest_lat`          DECIMAL(10,6)        NULL                COMMENT '下车点纬度',

  `status`            ENUM('pending','accepted','arrived','in_progress','completed','cancelled')
                      NOT NULL DEFAULT 'pending'              COMMENT '订单状态',

  -- 各状态节点时间（冗余列，服务 UI 时间线渲染）
  `created_at`        DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '下单/待接单时间',
  `accepted_at`       DATETIME             NULL                COMMENT '接单时间',
  `arrived_at`        DATETIME             NULL                COMMENT '司机到达时间',
  `started_at`        DATETIME             NULL                COMMENT '行程开始时间',
  `completed_at`      DATETIME             NULL                COMMENT '行程完成时间',
  `cancelled_at`      DATETIME             NULL                COMMENT '取消时间',
  `cancel_reason`     VARCHAR(200)         NULL                COMMENT '取消原因',
  `cancelled_by`      VARCHAR(20)          NULL                COMMENT '取消发起方：passenger/driver/admin/system',

  -- 计价规则溯源 + 价格快照（下单时刻固化，规则表后续变更不影响历史订单）
  `pricing_rule_id`       BIGINT UNSIGNED  NULL                COMMENT '下单时使用的规则',
  `rule_version`          INT UNSIGNED     NULL                COMMENT '快照：规则版本号',
  `snap_base_price`       DECIMAL(10,2)    NULL                COMMENT '快照：起步价',
  `snap_base_distance_km` DECIMAL(6,2)     NULL                COMMENT '快照：起步含里程',
  `snap_per_km_price`     DECIMAL(10,2)    NULL                COMMENT '快照：里程单价',
  `snap_per_min_price`    DECIMAL(10,2)    NULL                COMMENT '快照：时长单价',
  `snap_min_price`        DECIMAL(10,2)    NULL                COMMENT '快照：最低消费',

  `distance_km`       DECIMAL(8,3)         NULL                COMMENT '实际里程（km）',
  `duration_min`      DECIMAL(8,2)         NULL                COMMENT '实际时长（分钟）',
  `est_fare`          DECIMAL(10,2)        NULL                COMMENT '下单预估价（元）',
  `fare_amount`       DECIMAL(10,2)        NULL                COMMENT '最终车费（元）',
  `passenger_remark`  VARCHAR(200)         NULL                COMMENT '乘客备注',

  -- 生成列：仅「进行中」类订单取 driver_id，其余为 NULL
  -- 配合唯一索引，在数据库层强制「一个司机同时最多一个进行中订单」
  `active_driver_id`  BIGINT UNSIGNED
      GENERATED ALWAYS AS (
          CASE WHEN `status` IN ('accepted','arrived','in_progress')
               THEN `driver_id` ELSE NULL END
      ) STORED COMMENT '生成列：进行中订单=driver_id，其余NULL',

  `updated_at`        DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`order_id`),
  UNIQUE KEY `uk_order_no` (`order_no`),
  UNIQUE KEY `uk_active_driver` (`active_driver_id`),
  KEY `idx_order_pricing_rule` (`pricing_rule_id`),
  KEY `idx_order_vehicle` (`vehicle_id`),
  CONSTRAINT `ck_order_lng_o` CHECK (`origin_lng` IS NULL OR (`origin_lng` BETWEEN -180 AND 180)),
  CONSTRAINT `ck_order_lat_o` CHECK (`origin_lat` IS NULL OR (`origin_lat` BETWEEN  -90 AND  90)),
  CONSTRAINT `ck_order_lng_d` CHECK (`dest_lng`   IS NULL OR (`dest_lng`   BETWEEN -180 AND 180)),
  CONSTRAINT `ck_order_lat_d` CHECK (`dest_lat`   IS NULL OR (`dest_lat`   BETWEEN  -90 AND  90)),
  CONSTRAINT `ck_order_fare`  CHECK (`fare_amount` IS NULL OR `fare_amount` >= 0),
  CONSTRAINT `ck_order_cancelled_by`
      CHECK (`cancelled_by` IS NULL OR `cancelled_by` IN ('passenger','driver','admin','system')),
  CONSTRAINT `fk_order_passenger`   FOREIGN KEY (`passenger_id`)   REFERENCES `passenger` (`passenger_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  -- ⚠️ driver_id 是存储生成列 active_driver_id 的「基列」，
  --    MySQL 规定：基列的外键不得使用 ON UPDATE CASCADE / ON DELETE SET NULL
  --    （级联由存储引擎直接改行，会跳过生成列的重新计算）。
  --    故此处只能用 RESTRICT。driver_id 是代理主键、从不更新，语义上无任何损失。
  CONSTRAINT `fk_order_driver`      FOREIGN KEY (`driver_id`)      REFERENCES `driver` (`driver_id`)
      ON UPDATE RESTRICT ON DELETE RESTRICT,
  CONSTRAINT `fk_order_vehicle`     FOREIGN KEY (`vehicle_id`)     REFERENCES `vehicle` (`vehicle_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT `fk_order_car_type`    FOREIGN KEY (`car_type_id`)    REFERENCES `car_type` (`car_type_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT `fk_order_pricing_rule` FOREIGN KEY (`pricing_rule_id`) REFERENCES `pricing_rule` (`rule_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='订单主表';


CREATE TABLE `ride_order_status_log` (
  `log_id`        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '日志ID',
  `order_id`      BIGINT UNSIGNED NOT NULL                COMMENT '所属订单',
  `from_status`   VARCHAR(20)         NULL                COMMENT '变更前状态（首条为 NULL）',
  `to_status`     VARCHAR(20)     NOT NULL                COMMENT '变更后状态',
  `operator_type` VARCHAR(20)     NOT NULL                COMMENT '操作者类型：passenger/driver/admin/system',
  `operator_id`   BIGINT UNSIGNED     NULL                COMMENT '操作者ID',
  `changed_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '变更时间',
  `remark`        VARCHAR(200)        NULL                COMMENT '备注',
  PRIMARY KEY (`log_id`),
  KEY `idx_log_order_time` (`order_id`, `changed_at`),
  KEY `idx_log_to_status_time` (`to_status`, `changed_at`),
  CONSTRAINT `ck_log_operator_type`
      CHECK (`operator_type` IN ('passenger','driver','admin','system')),
  CONSTRAINT `ck_log_to_status`
      CHECK (`to_status` IN ('pending','accepted','arrived','in_progress','completed','cancelled')),
  CONSTRAINT `fk_log_order` FOREIGN KEY (`order_id`) REFERENCES `ride_order` (`order_id`)
      ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='订单状态变更历史表（审计/耗时统计）';


CREATE TABLE `order_dispatch` (
  `dispatch_id`  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '派单ID',
  `order_id`     BIGINT UNSIGNED NOT NULL                COMMENT '订单',
  `driver_id`    BIGINT UNSIGNED NOT NULL                COMMENT '被派司机',
  `status`       ENUM('invited','accepted','rejected','expired','cancelled')
                 NOT NULL DEFAULT 'invited'              COMMENT '邀请状态',
  `batch_no`     INT UNSIGNED    NOT NULL DEFAULT 1      COMMENT '派单批次（第1轮/第2轮…）',
  `invited_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '邀请时间',
  `responded_at` DATETIME            NULL                COMMENT '应答时间',
  `expire_at`    DATETIME            NULL                COMMENT '邀请过期时间',
  `distance_km`  DECIMAL(6,2)        NULL                COMMENT '派单时司机距上车点距离（排序用）',
  `remark`       VARCHAR(200)        NULL                COMMENT '备注',
  PRIMARY KEY (`dispatch_id`),
  UNIQUE KEY `uk_dispatch_order_driver` (`order_id`, `driver_id`),
  KEY `idx_dispatch_driver_status_time` (`driver_id`, `status`, `invited_at`),
  KEY `idx_dispatch_order_status` (`order_id`, `status`),
  KEY `idx_dispatch_status_expire` (`status`, `expire_at`),
  CONSTRAINT `ck_dispatch_distance` CHECK (`distance_km` IS NULL OR `distance_km` >= 0),
  CONSTRAINT `fk_dispatch_order`  FOREIGN KEY (`order_id`)  REFERENCES `ride_order` (`order_id`)
      ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `fk_dispatch_driver` FOREIGN KEY (`driver_id`) REFERENCES `driver` (`driver_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='派单/邀请记录表';


-- =============================================================
-- 六、选做：实时位置、轨迹、评价
-- =============================================================

CREATE TABLE `vehicle_location_current` (
  `vehicle_id` BIGINT UNSIGNED NOT NULL                COMMENT '车辆（一车一行）',
  `driver_id`  BIGINT UNSIGNED     NULL                COMMENT '当前司机',
  `order_id`   BIGINT UNSIGNED     NULL                COMMENT '当前执行订单（行程中非空）',
  `longitude`  DECIMAL(10,6)   NOT NULL                COMMENT '当前经度',
  `latitude`   DECIMAL(10,6)   NOT NULL                COMMENT '当前纬度',
  `speed`      DECIMAL(5,2)        NULL                COMMENT '速度 km/h',
  `heading`    DECIMAL(5,2)        NULL                COMMENT '航向角 0-360',
  `located_at` DATETIME(3)     NOT NULL                COMMENT '定位时间（毫秒精度）',
  `updated_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`vehicle_id`),
  KEY `idx_loc_order` (`order_id`),
  CONSTRAINT `ck_loc_lng`     CHECK (`longitude` BETWEEN -180 AND 180),
  CONSTRAINT `ck_loc_lat`     CHECK (`latitude`  BETWEEN  -90 AND  90),
  CONSTRAINT `ck_loc_heading` CHECK (`heading` IS NULL OR (`heading` >= 0 AND `heading` <= 360)),
  CONSTRAINT `fk_loc_vehicle` FOREIGN KEY (`vehicle_id`) REFERENCES `vehicle` (`vehicle_id`)
      ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `fk_loc_driver`  FOREIGN KEY (`driver_id`)  REFERENCES `driver` (`driver_id`)
      ON UPDATE CASCADE ON DELETE SET NULL,
  CONSTRAINT `fk_loc_order`   FOREIGN KEY (`order_id`)   REFERENCES `ride_order` (`order_id`)
      ON UPDATE CASCADE ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='车辆当前定位表（热写，UPSERT）';


CREATE TABLE `order_track` (
  `track_id`   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '轨迹点ID',
  `order_id`   BIGINT UNSIGNED NOT NULL                COMMENT '所属订单',
  `longitude`  DECIMAL(10,6)   NOT NULL                COMMENT '经度',
  `latitude`   DECIMAL(10,6)   NOT NULL                COMMENT '纬度',
  `speed`      DECIMAL(5,2)        NULL                COMMENT '速度 km/h',
  `located_at` DATETIME(3)     NOT NULL                COMMENT '采样时间（毫秒精度）',
  PRIMARY KEY (`track_id`),
  KEY `idx_track_order_time` (`order_id`, `located_at`),
  CONSTRAINT `ck_track_lng` CHECK (`longitude` BETWEEN -180 AND 180),
  CONSTRAINT `ck_track_lat` CHECK (`latitude`  BETWEEN  -90 AND  90),
  CONSTRAINT `fk_track_order` FOREIGN KEY (`order_id`) REFERENCES `ride_order` (`order_id`)
      ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='行程轨迹点表（仅行程中写入，只保留一个复合索引以降低写放大）';


CREATE TABLE `order_review` (
  `review_id`    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '评价ID',
  `order_id`     BIGINT UNSIGNED NOT NULL                COMMENT '订单（唯一 -> 一单一评）',
  `passenger_id` BIGINT UNSIGNED NOT NULL                COMMENT '评价人',
  `driver_id`    BIGINT UNSIGNED NOT NULL                COMMENT '被评价司机',
  `score`        TINYINT         NOT NULL                COMMENT '评分 1-5',
  `content`      VARCHAR(500)        NULL                COMMENT '评价内容',
  `tags`         VARCHAR(200)        NULL                COMMENT '标签，逗号分隔',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '评价时间',
  PRIMARY KEY (`review_id`),
  UNIQUE KEY `uk_review_order` (`order_id`),
  KEY `idx_review_driver_time` (`driver_id`, `created_at`),
  KEY `idx_review_passenger` (`passenger_id`),
  CONSTRAINT `ck_review_score` CHECK (`score` BETWEEN 1 AND 5),
  CONSTRAINT `fk_review_order`     FOREIGN KEY (`order_id`)     REFERENCES `ride_order` (`order_id`)
      ON UPDATE CASCADE ON DELETE CASCADE,
  CONSTRAINT `fk_review_passenger` FOREIGN KEY (`passenger_id`) REFERENCES `passenger` (`passenger_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT,
  CONSTRAINT `fk_review_driver`    FOREIGN KEY (`driver_id`)    REFERENCES `driver` (`driver_id`)
      ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='订单评价表';
