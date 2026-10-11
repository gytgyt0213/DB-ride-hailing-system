-- =============================================================
-- 04_triggers_procedures.sql
-- 网约车（打车）系统 —— 存储函数 / 存储过程 / 触发器
--
-- 执行方式（⚠️ 必须用 mysql 命令行客户端，不能走 pymysql）：
--     mysql -u root -p --default-character-set=utf8mb4 < sql/04_triggers_procedures.sql
--
-- 【为什么必须用 CLI】
--   DELIMITER 是 mysql 客户端指令，不是服务器 SQL 语句。
--   pymysql 逐条发送语句，不认识 DELIMITER，会把 BEGIN...END 里的分号
--   当成语句结束，导致语法错误。
--
-- 【权限前提】
--   本机 log_bin=1 且 log_bin_trust_function_creators=0，
--   创建「存储函数」和「触发器」需要 SUPER 权限（否则报 ERROR 1418）。
--   root 拥有 SUPER，故本脚本用 root 执行。
-- =============================================================

USE `taxi_system`;
SET NAMES utf8mb4;

DROP TRIGGER  IF EXISTS `trg_review_after_insert`;
DROP PROCEDURE IF EXISTS `sp_create_order`;
DROP PROCEDURE IF EXISTS `sp_dispatch_order`;
DROP PROCEDURE IF EXISTS `sp_accept_order`;
DROP PROCEDURE IF EXISTS `sp_complete_order`;
DROP PROCEDURE IF EXISTS `sp_cancel_order`;
DROP PROCEDURE IF EXISTS `sp_expire_dispatch`;
DROP FUNCTION  IF EXISTS `fn_calc_fare`;

DELIMITER $$

-- =============================================================
-- 一、存储函数：计价
--     计价的「唯一真相来源」——应用层不再自己算钱，
--     一律调用 SELECT fn_calc_fare(...)，避免前后端各算一套。
--
--     公式：
--       fare = 起步价
--            + MAX(0, 里程 - 起步含里程) * 里程单价
--            + 时长 * 时长单价
--       fare = MAX(fare, 最低消费)
-- =============================================================
CREATE FUNCTION `fn_calc_fare`(
    p_base_price       DECIMAL(10,2),   -- 起步价
    p_base_distance_km DECIMAL(6,2),    -- 起步价包含的里程
    p_per_km_price     DECIMAL(10,2),   -- 里程单价（元/km）
    p_per_min_price    DECIMAL(10,2),   -- 时长单价（元/min）
    p_min_price        DECIMAL(10,2),   -- 最低消费
    p_distance_km      DECIMAL(8,3),    -- 实际里程
    p_duration_min     DECIMAL(8,2)     -- 实际时长（分钟）
) RETURNS DECIMAL(10,2)
    DETERMINISTIC
    NO SQL
    COMMENT '车费计算：起步价 + 超起步里程*里程单价 + 时长*时长单价，再以最低消费兜底'
BEGIN
    DECLARE v_distance  DECIMAL(8,3);
    DECLARE v_duration  DECIMAL(8,2);
    DECLARE v_fare      DECIMAL(12,4);

    SET v_distance = IFNULL(p_distance_km, 0);
    SET v_duration = IFNULL(p_duration_min, 0);

    SET v_fare = IFNULL(p_base_price, 0)
               + GREATEST(0, v_distance - IFNULL(p_base_distance_km, 0)) * IFNULL(p_per_km_price, 0)
               + v_duration * IFNULL(p_per_min_price, 0);

    IF v_fare < IFNULL(p_min_price, 0) THEN
        SET v_fare = p_min_price;
    END IF;

    RETURN ROUND(v_fare, 2);
END$$


-- =============================================================
-- 二、存储过程：下单
--     一个事务内完成：取当前有效计价规则 -> 价格快照 -> 插订单 -> 写首条状态日志
--     这是「金额快照」设计的落地：把规则价格复制进订单行，
--     规则表后续怎么改版本，都不影响这张历史订单。
-- =============================================================
CREATE PROCEDURE `sp_create_order`(
    IN  p_passenger_id   BIGINT UNSIGNED,
    IN  p_car_type_id    TINYINT UNSIGNED,
    IN  p_origin_address VARCHAR(200),
    IN  p_origin_lng     DECIMAL(10,6),
    IN  p_origin_lat     DECIMAL(10,6),
    IN  p_origin_region  VARCHAR(50),
    IN  p_dest_address   VARCHAR(200),
    IN  p_dest_lng       DECIMAL(10,6),
    IN  p_dest_lat       DECIMAL(10,6),
    IN  p_remark         VARCHAR(200),
    OUT p_order_id       BIGINT UNSIGNED,
    OUT p_order_no       VARCHAR(32),
    OUT p_result_code    VARCHAR(30),
    OUT p_result_msg     VARCHAR(200)
)
    MODIFIES SQL DATA
    COMMENT '下单：快照计价规则 + 创建待接单订单 + 写首条状态日志'
BEGIN
    DECLARE v_rule_id        BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_rule_version   INT UNSIGNED    DEFAULT NULL;
    DECLARE v_base_price     DECIMAL(10,2)   DEFAULT NULL;
    DECLARE v_base_km        DECIMAL(6,2)    DEFAULT NULL;
    DECLARE v_per_km         DECIMAL(10,2)   DEFAULT NULL;
    DECLARE v_per_min        DECIMAL(10,2)   DEFAULT NULL;
    DECLARE v_min_price      DECIMAL(10,2)   DEFAULT NULL;
    DECLARE v_est_fare       DECIMAL(10,2)   DEFAULT NULL;
    DECLARE v_tmp_no         VARCHAR(32)     DEFAULT NULL;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_rule_id = NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_order_id    = NULL;
        SET p_order_no    = NULL;
        SET p_result_code = 'DB_ERROR';
        SET p_result_msg  = '下单失败：数据库异常，事务已回滚';
    END;

    SET p_order_id    = NULL;
    SET p_order_no    = NULL;
    SET p_result_code = 'OK';
    SET p_result_msg  = '下单成功';

    START TRANSACTION;

    -- 1) 取该车型当前生效的计价规则
    SELECT r.rule_id, r.version, r.base_price, r.base_distance_km,
           r.per_km_price, r.per_min_price, r.min_price
      INTO v_rule_id, v_rule_version, v_base_price, v_base_km,
           v_per_km, v_per_min, v_min_price
      FROM v_pricing_current r
     WHERE r.car_type_id = p_car_type_id
     ORDER BY r.version DESC
     LIMIT 1;

    IF v_rule_id IS NULL THEN
        ROLLBACK;
        SET p_result_code = 'NO_PRICING_RULE';
        SET p_result_msg  = CONCAT('车型 ', p_car_type_id, ' 当前没有生效的计价规则，无法下单');
    ELSE
        -- 2) 插入订单（价格快照一并写入）
        --    order_no 需等自增 ID 生成后才能拼出，故先用唯一临时串占位
        SET v_tmp_no = CONCAT('TMP-', UUID_SHORT());

        INSERT INTO `ride_order` (
            `order_no`, `passenger_id`, `car_type_id`,
            `origin_address`, `origin_lng`, `origin_lat`, `origin_region`,
            `dest_address`,   `dest_lng`,   `dest_lat`,
            `status`,
            `pricing_rule_id`, `rule_version`,
            `snap_base_price`, `snap_base_distance_km`,
            `snap_per_km_price`, `snap_per_min_price`, `snap_min_price`,
            `passenger_remark`
        ) VALUES (
            v_tmp_no, p_passenger_id, p_car_type_id,
            p_origin_address, p_origin_lng, p_origin_lat, IFNULL(p_origin_region, 'default'),
            p_dest_address,   p_dest_lng,   p_dest_lat,
            'pending',
            v_rule_id, v_rule_version,
            v_base_price, v_base_km,
            v_per_km, v_per_min, v_min_price,
            p_remark
        );

        SET p_order_id = LAST_INSERT_ID();

        -- 3) 回填可读的业务订单号：RO + 日期 + 8位订单ID
        SET p_order_no = CONCAT('RO', DATE_FORMAT(NOW(), '%Y%m%d'), LPAD(p_order_id, 8, '0'));
        UPDATE `ride_order` SET `order_no` = p_order_no WHERE `order_id` = p_order_id;

        -- 4) 写首条状态变更日志（from_status 为 NULL 表示创建）
        INSERT INTO `ride_order_status_log`
            (`order_id`, `from_status`, `to_status`, `operator_type`, `operator_id`, `remark`)
        VALUES
            (p_order_id, NULL, 'pending', 'passenger', p_passenger_id, '乘客发起叫车');

        COMMIT;
    END IF;
END$$


-- =============================================================
-- 三、存储过程：派单
--     从 v_available_drivers（已有资格的司机）中按「同区域 + 持有该车型可运营车辆」
--     挑选候选，写入 order_dispatch 邀请记录。
--     分批派单：batch_no 递增，支持「第一轮没人接 -> 再派一轮」。
-- =============================================================
CREATE PROCEDURE `sp_dispatch_order`(
    IN  p_order_id    BIGINT UNSIGNED,
    IN  p_batch_size  INT UNSIGNED,
    OUT p_batch_no    INT UNSIGNED,
    OUT p_invited     INT,
    OUT p_result_code VARCHAR(30),
    OUT p_result_msg  VARCHAR(200)
)
    MODIFIES SQL DATA
    COMMENT '派单：按区域+车型从可接单司机中选候选并写入邀请记录'
BEGIN
    DECLARE v_region   VARCHAR(50)      DEFAULT NULL;
    DECLARE v_car_type TINYINT UNSIGNED DEFAULT NULL;
    DECLARE v_status   VARCHAR(20)      DEFAULT NULL;
    DECLARE v_size     INT UNSIGNED     DEFAULT 3;

    DECLARE CONTINUE HANDLER FOR NOT FOUND
        SET v_region = NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_result_code = 'DB_ERROR';
        SET p_result_msg  = '派单失败：数据库异常，事务已回滚';
    END;

    SET p_batch_no    = 0;
    SET p_invited     = 0;
    SET p_result_code = 'OK';
    SET p_result_msg  = '派单完成';
    SET v_size        = IFNULL(p_batch_size, 3);
    IF v_size = 0 OR v_size > 20 THEN
        SET v_size = 3;
    END IF;

    START TRANSACTION;

    SELECT o.`origin_region`, o.`car_type_id`, o.`status`
      INTO v_region, v_car_type, v_status
      FROM `ride_order` o
     WHERE o.`order_id` = p_order_id
       FOR UPDATE;

    IF v_region IS NULL AND v_status IS NULL THEN
        ROLLBACK;
        SET p_result_code = 'ORDER_NOT_FOUND';
        SET p_result_msg  = CONCAT('订单 ', p_order_id, ' 不存在');
    ELSEIF v_status <> 'pending' THEN
        ROLLBACK;
        SET p_result_code = 'ORDER_NOT_PENDING';
        SET p_result_msg  = CONCAT('订单当前状态为 ', v_status, '，不可派单');
    ELSE
        SELECT IFNULL(MAX(d.`batch_no`), 0) + 1
          INTO p_batch_no
          FROM `order_dispatch` d
         WHERE d.`order_id` = p_order_id;

        INSERT INTO `order_dispatch`
            (`order_id`, `driver_id`, `status`, `batch_no`, `invited_at`, `expire_at`)
        SELECT p_order_id,
               ad.`driver_id`,
               'invited',
               p_batch_no,
               NOW(),
               DATE_ADD(NOW(), INTERVAL 60 SECOND)
          FROM `v_available_drivers` ad
         WHERE ad.`region` = v_region
           AND EXISTS (SELECT 1
                         FROM `vehicle` v
                        WHERE v.`driver_id`     = ad.`driver_id`
                          AND v.`car_type_id`   = v_car_type
                          AND v.`operate_status` = 1)
           AND NOT EXISTS (SELECT 1
                             FROM `order_dispatch` od
                            WHERE od.`order_id`  = p_order_id
                              AND od.`driver_id` = ad.`driver_id`)
         ORDER BY ad.`service_score` DESC, ad.`driver_id` ASC
         LIMIT v_size;

        SET p_invited = ROW_COUNT();
        COMMIT;

        IF p_invited = 0 THEN
            SET p_result_code = 'NO_CANDIDATE';
            SET p_result_msg  = CONCAT('区域 ', v_region, ' 内暂无符合条件的可派单司机');
        ELSE
            SET p_result_msg = CONCAT('第 ', p_batch_no, ' 轮派单，已邀请 ', p_invited, ' 名司机');
        END IF;
    END IF;
END$$


-- =============================================================
-- 四、存储过程：接单（并发控制核心）
--
--     「两个司机同时抢同一订单」的解法（加锁顺序是关键）：
--
--       第 1 层：UPDATE ... WHERE status='pending' + 判断 ROW_COUNT()
--                —— 必须【最先】执行。行锁天然串行化：并发事务在此阻塞，
--                   先提交者赢；后到者重新读时 status 已不是 pending，匹配 0 行。
--                   把它放在最前面，落败事务根本不会去申请 driver/vehicle 的锁，
--                   从而避免跨表交叉持锁形成的死锁环。
--       第 2 层：SELECT ... FOR UPDATE 复检司机与车辆资格
--                （需求6：只有车辆处于可运营状态时才能接单；
--                  防止「被邀请时有资格，接单瞬间车已停运/司机已下线」。
--                  此时已无同单竞争者，这一层的锁不再产生死锁）
--       第 3 层：ride_order.uk_active_driver 生成列唯一索引
--                数据库层兜底「一个司机同时最多一个进行中订单」，
--                触发时由 1062 处理器转为 DRIVER_BUSY 业务语义
--
--     ⚠️ 关键纪律：绝不能写成「先 SELECT 查状态，再无条件 UPDATE」。
--
--     历史注记：早期版本把 FOR UPDATE 资格复检放在 UPDATE 之前，
--     5 并发实测稳定报 ERROR 1213 死锁（InnoDB 报告显示事务持有 driver 行锁
--     等待 ride_order 行锁，形成循环）。调整加锁顺序后死锁消失。
--     详见 docs/实验报告.md 的「并发控制」章节。
-- =============================================================
CREATE PROCEDURE `sp_accept_order`(
    IN  p_order_id    BIGINT UNSIGNED,
    IN  p_driver_id   BIGINT UNSIGNED,
    OUT p_vehicle_id  BIGINT UNSIGNED,
    OUT p_result_code VARCHAR(30),
    OUT p_result_msg  VARCHAR(200)
)
    MODIFIES SQL DATA
    COMMENT '司机接单：资格复检 + 条件抢占 + 撤销其他邀请 + 写状态日志'
BEGIN
    DECLARE v_vehicle_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_invited    INT DEFAULT 0;

    -- ⚠️ 这两个变量必须声明在「过程主体」而不是 handler 的 BEGIN...END 里。
    --    MySQL 的 GET DIAGNOSTICS 不会填充在 handler 块内部声明的局部变量
    --    （实测：声明在 handler 内 → 取到 errno=0、消息为空；
    --           声明在主体中 → 正确取到 errno=1146 及消息）。
    DECLARE v_errno  INT          DEFAULT 0;
    DECLARE v_errmsg VARCHAR(255) DEFAULT '';

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_vehicle_id = NULL;

    -- 1062 = 唯一键冲突。此处必然来自 ride_order.uk_active_driver
    -- （生成列唯一索引「一个司机同时最多一个进行中订单」），
    -- 这是数据库层的兜底防线被触发，要给出明确的业务语义而非「数据库异常」。
    DECLARE EXIT HANDLER FOR 1062
    BEGIN
        ROLLBACK;
        SET p_vehicle_id  = NULL;
        SET p_result_code = 'DRIVER_BUSY';
        SET p_result_msg  = '接单失败：您已有进行中的订单，不能同时接多单';
    END;

    -- 其它异常：捕获真实错误码与信息，便于诊断
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_errno = MYSQL_ERRNO, v_errmsg = MESSAGE_TEXT;
        ROLLBACK;
        SET p_vehicle_id  = NULL;
        SET p_result_code = CONCAT('DB_ERROR_', v_errno);
        SET p_result_msg  = CONCAT('接单失败[', v_errno, ']：', v_errmsg);
    END;

    SET p_vehicle_id  = NULL;
    SET p_result_code = 'OK';
    SET p_result_msg  = '接单成功';

    START TRANSACTION;

    -- 第 0 层：既有订单归属确认 —— 该司机是否收到过本单的有效邀请
    --         （非锁定读，只用于快速排除明显不合法的调用）
    SELECT COUNT(*) INTO v_invited
      FROM `order_dispatch` od
     WHERE od.`order_id`  = p_order_id
       AND od.`driver_id` = p_driver_id
       AND od.`status`    = 'invited';

    IF v_invited = 0 THEN
        ROLLBACK;
        SET p_result_code = 'NOT_INVITED';
        SET p_result_msg  = '该订单未向此司机发出有效邀请，无法接单';
    ELSE
        -- =========================================================
        -- 第 1 层：条件抢占 —— 必须最先执行，先锁住竞争最激烈的 ride_order 行
        --
        -- 【为什么顺序如此重要】
        --   早期版本先对 driver/vehicle 做 SELECT ... FOR UPDATE，再 UPDATE
        --   ride_order，实测在 5 个司机并发时稳定触发死锁（ERROR 1213）：
        --   各事务都先持有 driver 行锁、再去等 ride_order 行锁，
        --   在两张表之间形成交叉持锁的循环等待。
        --   改成「先抢 ride_order 行」后，竞争在第一步就被行锁串行化：
        --   只有一个事务能拿到该行并继续往下走，其余事务在此处等待，
        --   醒来时 status 已变为 accepted、匹配 0 行而干净退出，
        --   根本不会去申请 driver/vehicle 的锁，死锁环无从形成。
        --
        --   注意：WHERE status='pending' 这个守卫是并发正确性的关键，
        --   绝不能写成「先 SELECT 查状态，再无条件 UPDATE」。
        -- =========================================================
        UPDATE `ride_order`
           SET `status`     = 'accepted',
               `driver_id`  = p_driver_id,
               `accepted_at` = NOW()
         WHERE `order_id` = p_order_id
           AND `status`   = 'pending';

        IF ROW_COUNT() = 0 THEN
            -- 影响行数为 0：订单已被别人抢走（或状态已变）
            ROLLBACK;
            SET p_vehicle_id  = NULL;
            SET p_result_code = 'ALREADY_TAKEN';
            SET p_result_msg  = '接单失败：订单已被其他司机接走';
        ELSE
            -- =====================================================
            -- 第 2 层：资格复检 + 取一辆可运营车辆
            --
            --   走到这里说明本事务已经独占该订单，不会有并发者在同一订单上
            --   与它争抢 driver/vehicle 的锁，因此这一层的 FOR UPDATE 是安全的。
            --   仍然需要 FOR UPDATE：要在本事务结束前锁住车辆行，
            --   使「管理员把该车改为不可运营」的事务必须等待，
            --   保证需求6「只有车辆处于可运营状态时才能接单」
            --   在「被邀请 → 接单」这个时间窗内始终成立。
            -- =====================================================
            SELECT v.`vehicle_id`
              INTO v_vehicle_id
              FROM `driver`  d
              JOIN `account` a ON a.`account_id` = d.`account_id`
              JOIN `vehicle` v ON v.`driver_id`  = d.`driver_id`
             WHERE d.`driver_id`     = p_driver_id
               AND d.`online_status` = 1
               AND d.`status`        = 1
               AND d.`audit_status`  = 1
               AND a.`status`        = 1
               AND v.`operate_status` = 1
             ORDER BY v.`vehicle_id` ASC
             LIMIT 1
               FOR UPDATE;

            IF v_vehicle_id IS NULL THEN
                -- 资格在「被邀请 → 接单」期间失效：整笔回滚，
                -- 订单退回 pending，本司机的邀请保持 invited 状态，
                -- 其他司机仍可正常接单
                ROLLBACK;
                SET p_result_code = 'NOT_QUALIFIED';
                SET p_result_msg  = '接单失败：司机未在线/未通过审核，或名下没有可运营车辆';
            ELSE
                -- 回填执行车辆
                UPDATE `ride_order`
                   SET `vehicle_id` = v_vehicle_id
                 WHERE `order_id` = p_order_id;

                -- 本司机的邀请置为已接受
                UPDATE `order_dispatch`
                   SET `status` = 'accepted', `responded_at` = NOW()
                 WHERE `order_id` = p_order_id
                   AND `driver_id` = p_driver_id;

                -- 同订单其它仍待应答的邀请全部取消
                UPDATE `order_dispatch`
                   SET `status` = 'cancelled', `responded_at` = NOW()
                 WHERE `order_id` = p_order_id
                   AND `driver_id` <> p_driver_id
                   AND `status` = 'invited';

                -- 写状态变更日志
                INSERT INTO `ride_order_status_log`
                    (`order_id`, `from_status`, `to_status`, `operator_type`, `operator_id`, `remark`)
                VALUES
                    (p_order_id, 'pending', 'accepted', 'driver', p_driver_id, '司机接单');

                SET p_vehicle_id = v_vehicle_id;
                COMMIT;
            END IF;
        END IF;
    END IF;
END$$


-- =============================================================
-- 五、存储过程：完成行程并结算
--     用订单自己的快照列调 fn_calc_fare 算钱 —— 规则表怎么改都不影响本单。
-- =============================================================
CREATE PROCEDURE `sp_complete_order`(
    IN  p_order_id     BIGINT UNSIGNED,
    IN  p_driver_id    BIGINT UNSIGNED,
    IN  p_distance_km  DECIMAL(8,3),
    IN  p_duration_min DECIMAL(8,2),
    OUT p_fare_amount  DECIMAL(10,2),
    OUT p_result_code  VARCHAR(30),
    OUT p_result_msg   VARCHAR(200)
)
    MODIFIES SQL DATA
    COMMENT '完成行程：按订单快照列计价 + 改状态 + 写日志'
BEGIN
    DECLARE v_status   VARCHAR(20)   DEFAULT NULL;
    DECLARE v_driver   BIGINT UNSIGNED DEFAULT NULL;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_status = NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_fare_amount = NULL;
        SET p_result_code = 'DB_ERROR';
        SET p_result_msg  = '结算失败：数据库异常，事务已回滚';
    END;

    SET p_fare_amount = NULL;
    SET p_result_code = 'OK';
    SET p_result_msg  = '行程已完成';

    START TRANSACTION;

    SELECT o.`status`, o.`driver_id`
      INTO v_status, v_driver
      FROM `ride_order` o
     WHERE o.`order_id` = p_order_id
       FOR UPDATE;

    IF v_status IS NULL THEN
        ROLLBACK;
        SET p_result_code = 'ORDER_NOT_FOUND';
        SET p_result_msg  = CONCAT('订单 ', p_order_id, ' 不存在');
    ELSEIF v_status <> 'in_progress' THEN
        ROLLBACK;
        SET p_result_code = 'INVALID_STATUS';
        SET p_result_msg  = CONCAT('订单当前状态为 ', v_status, '，只有「行程中」的订单才能完成');
    ELSEIF v_driver <> p_driver_id THEN
        ROLLBACK;
        SET p_result_code = 'NOT_OWN_ORDER';
        SET p_result_msg  = '该订单不属于此司机';
    ELSE
        -- 用订单快照列计价（唯一真相来源）
        SELECT `fn_calc_fare`(
                   o.`snap_base_price`,
                   o.`snap_base_distance_km`,
                   o.`snap_per_km_price`,
                   o.`snap_per_min_price`,
                   o.`snap_min_price`,
                   p_distance_km,
                   p_duration_min)
          INTO p_fare_amount
          FROM `ride_order` o
         WHERE o.`order_id` = p_order_id;

        UPDATE `ride_order`
           SET `status`       = 'completed',
               `completed_at` = NOW(),
               `distance_km`  = p_distance_km,
               `duration_min` = p_duration_min,
               `fare_amount`  = p_fare_amount
         WHERE `order_id` = p_order_id;

        INSERT INTO `ride_order_status_log`
            (`order_id`, `from_status`, `to_status`, `operator_type`, `operator_id`, `remark`)
        VALUES
            (p_order_id, 'in_progress', 'completed', 'driver', p_driver_id,
             CONCAT('行程结束，里程 ', p_distance_km, ' km，时长 ', p_duration_min, ' 分钟'));

        COMMIT;
        SET p_result_msg = CONCAT('行程已完成，车费 ', p_fare_amount, ' 元');
    END IF;
END$$


-- =============================================================
-- 六、存储过程：取消订单
--     乘客/管理员/系统在「待接单 / 已接单 / 司机已到达」阶段可取消。
-- =============================================================
CREATE PROCEDURE `sp_cancel_order`(
    IN  p_order_id     BIGINT UNSIGNED,
    IN  p_operator     VARCHAR(20),
    IN  p_operator_id  BIGINT UNSIGNED,
    IN  p_reason       VARCHAR(200),
    OUT p_result_code  VARCHAR(30),
    OUT p_result_msg   VARCHAR(200)
)
    MODIFIES SQL DATA
    COMMENT '取消订单：状态守卫 + 写日志 + 关闭未应答邀请'
BEGIN
    DECLARE v_status VARCHAR(20) DEFAULT NULL;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_status = NULL;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        SET p_result_code = 'DB_ERROR';
        SET p_result_msg  = '取消失败：数据库异常，事务已回滚';
    END;

    SET p_result_code = 'OK';
    SET p_result_msg  = '订单已取消';

    START TRANSACTION;

    SELECT o.`status` INTO v_status
      FROM `ride_order` o
     WHERE o.`order_id` = p_order_id
       FOR UPDATE;

    IF v_status IS NULL THEN
        ROLLBACK;
        SET p_result_code = 'ORDER_NOT_FOUND';
        SET p_result_msg  = CONCAT('订单 ', p_order_id, ' 不存在');
    ELSEIF v_status NOT IN ('pending', 'accepted', 'arrived') THEN
        ROLLBACK;
        SET p_result_code = 'INVALID_STATUS';
        SET p_result_msg  = CONCAT('订单当前状态为 ', v_status, '，不可取消');
    ELSE
        UPDATE `ride_order`
           SET `status`       = 'cancelled',
               `cancelled_at` = NOW(),
               `cancel_reason` = p_reason,
               `cancelled_by`  = p_operator
         WHERE `order_id` = p_order_id;

        INSERT INTO `ride_order_status_log`
            (`order_id`, `from_status`, `to_status`, `operator_type`, `operator_id`, `remark`)
        VALUES
            (p_order_id, v_status, 'cancelled', p_operator, p_operator_id, p_reason);

        UPDATE `order_dispatch`
           SET `status` = 'cancelled', `responded_at` = NOW()
         WHERE `order_id` = p_order_id
           AND `status` = 'invited';

        COMMIT;
    END IF;
END$$


-- =============================================================
-- 七、存储过程：批量清理过期派单邀请
--     配合 MySQL EVENT 定时调用（选做：证照到期提醒同属定时任务）
-- =============================================================
CREATE PROCEDURE `sp_expire_dispatch`(
    OUT p_expired INT
)
    MODIFIES SQL DATA
    COMMENT '把已超时未应答的派单邀请批量置为 expired'
BEGIN
    UPDATE `order_dispatch`
       SET `status` = 'expired'
     WHERE `status` = 'invited'
       AND `expire_at` IS NOT NULL
       AND `expire_at` < NOW();

    SET p_expired = ROW_COUNT();
END$$


-- =============================================================
-- 八、触发器：评价后维护司机服务分
--
--     只做这一件事。理由：纯单表派生维护，不依赖「谁操作的」这类会话上下文。
--
--     【为什么是「回读求精确值」而不是「增量公式」】
--       最初用的是增量公式：
--          新均值 = (旧均值 × 旧评价数 + 本次评分) ÷ (旧评价数 + 1)
--       它每一步都要 ROUND 到 2 位小数，误差会逐次累积。
--       实测：某司机 15 条评价，精确均值 4.2667，
--             增量公式得到 4.25 —— 累积偏差 0.02，
--       导致管理端的「服务分一致性校验」显示出「不一致」，
--       看起来像是系统出了 bug。
--
--       改为回读 order_review 求精确均值后，结果与 ROUND(AVG(score),2) 完全相等。
--
--     【关于「触发器不能读自己的表」的误解】
--       MySQL 只禁止触发器**修改**它所在的那张表
--       （ERROR 1442: Can't update table ... already used by statement），
--       **不禁止读取**。本项目实测验证过：
--       在 AFTER INSERT 触发器里 SELECT 本表，新插入的行已经可见，
--       因此求出的均值天然包含本次评价，无需特判。
--
--     【性能权衡】
--       回读是 O(n) 而增量是 O(1)。本场景每次评价仅扫该司机的评价行
--       （几十条量级），代价可忽略；正确性远比这点开销重要。
--       若评价量真到百万级，可改回增量 + 定期重算校正。
--
--     ⚠️ 刻意不用触发器写「订单状态日志」：
--        触发器拿不到操作者 operator_id，需靠 SET @变量 传递会话上下文，
--        脆弱且难调试。状态日志改由应用层在同一事务内显式写入。
-- =============================================================
CREATE TRIGGER `trg_review_after_insert`
AFTER INSERT ON `order_review`
FOR EACH ROW
BEGIN
    UPDATE `driver` d
       SET d.`service_score` = (
               SELECT ROUND(AVG(r.`score`), 2)
                 FROM `order_review` r
                WHERE r.`driver_id` = NEW.`driver_id`
           ),
           d.`review_count` = (
               SELECT COUNT(*)
                 FROM `order_review` r
                WHERE r.`driver_id` = NEW.`driver_id`
           )
     WHERE d.`driver_id` = NEW.`driver_id`;
END$$


DELIMITER ;


-- =============================================================
-- 验证：列出已建立的对象
-- =============================================================
SELECT ROUTINE_TYPE AS `类型`, ROUTINE_NAME AS `名称`
FROM information_schema.ROUTINES
WHERE ROUTINE_SCHEMA = 'taxi_system'
UNION ALL
SELECT 'TRIGGER', TRIGGER_NAME
FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA = 'taxi_system'
ORDER BY `类型`, `名称`;
