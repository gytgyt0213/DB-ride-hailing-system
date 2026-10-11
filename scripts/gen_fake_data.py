# -*- coding: utf-8 -*-
"""演示数据生成器 —— 为实验报告与统计报表造出足量、真实感强的数据。

运行：
    F:\\pythonnewversion\\py39ver\\python.exe scripts\\gen_fake_data.py
    F:\\pythonnewversion\\py39ver\\python.exe scripts\\gen_fake_data.py --reset   # 先清空再生成

产出规模（可通过下方常量调整）：
    乘客 50 / 司机 30 / 车辆 34 / 订单 900+
    以及对应的状态日志、派单记录、行程轨迹、评价、常用地址、车辆定位

【为什么用 Python 生成而不是写死的 SQL】
    1. 订单状态与时间线要符合业务逻辑（时间必须递增、状态必须自洽）
    2. 车费要通过数据库里的 fn_calc_fare 计算，保证与系统「唯一真相来源」一致
    3. 要覆盖各状态、各时段、各区域的分布，写死 SQL 不现实

【关键约束（生成时必须遵守）】
    * ride_order.uk_active_driver —— 一个司机同时最多一个「进行中」订单，
      因此 in-flight 订单的司机不能重复分配
    * STRICT_TRANS_TABLES —— 所有 NOT NULL 无默认值的列都必须显式给值
    * 时间线必须单调递增，否则 v_order_status_duration 会算出负值
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402
from pymysql.cursors import DictCursor  # noqa: E402

from config import DBConfig  # noqa: E402
from security.password import hash_password  # noqa: E402

# -------------------------------------------------------------
# 规模与概率参数
# -------------------------------------------------------------
N_PASSENGERS = 50
N_DRIVERS = 30
N_ORDERS = 900
DAYS_BACK = 120           # 订单散布在最近 120 天内
DEMO_PASSWORD = "123456"  # 所有演示账号的统一密码，便于演示登录
RANDOM_SEED = 20261004    # 固定种子，保证数据可复现

# 订单状态分布（权重）
STATUS_WEIGHTS = [
    ("completed",   78),
    ("cancelled",   12),
    ("pending",      4),
    ("accepted",     3),
    ("arrived",      2),
    ("in_progress",  1),
]

REGIONS = [
    "朝阳区", "海淀区", "东城区", "西城区",
    "丰台区", "通州区", "昌平区", "大兴区",
]

PLACE_TEMPLATES = [
    "{region}万达广场", "{region}地铁站", "{region}人民医院", "{region}火车站",
    "{region}科技园区", "{region}大学城", "{region}国际机场", "{region}体育中心",
    "{region}购物中心", "{region}公园北门", "{region}软件园", "{region}会展中心",
]

CAR_TYPE_IDS = [1, 2, 3, 4]
CAR_TYPE_WEIGHTS = [55, 28, 12, 5]   # 经济型最常见

BRANDS = [
    ("大众", "朗逸"), ("丰田", "卡罗拉"), ("本田", "雅阁"), ("日产", "轩逸"),
    ("别克", "英朗"), ("现代", "伊兰特"), ("比亚迪", "秦PLUS"), ("吉利", "帝豪"),
    ("奥迪", "A6L"), ("宝马", "5系"), ("奔驰", "E级"), ("特斯拉", "Model 3"),
]
COLORS = ["白色", "黑色", "银色", "灰色", "蓝色", "红色"]
SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = "伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英"

REVIEW_TAGS = [
    "准时", "车内整洁", "态度好", "驾驶平稳", "路线合理",
    "主动帮忙", "空调舒适", "沟通顺畅",
]
REVIEW_TEXTS_GOOD = [
    "司机很准时，全程驾驶平稳，非常满意。",
    "师傅态度很好，还帮我搬了行李，好评！",
    "车内很干净，空调温度合适，体验不错。",
    "路线选择合理，没有绕路，准时到达。",
    "沟通顺畅，师傅提前打电话确认了位置。",
]
REVIEW_TEXTS_MID = [
    "整体还可以，就是等了有点久。",
    "司机师傅态度还行，但车内略有些味道。",
    "行程正常，没什么特别的。",
]
REVIEW_TEXTS_BAD = [
    "等了很久才到，体验一般。",
    "司机绕了路，到达时间比预计晚了不少。",
    "车内卫生状况有待改善。",
]


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DBConfig.host, port=DBConfig.port,
        user=DBConfig.user, password=DBConfig.password,
        database=DBConfig.database, charset=DBConfig.charset,
        cursorclass=DictCursor, autocommit=False,
    )


def random_name() -> str:
    return random.choice(SURNAMES) + random.choice(GIVEN) + "".join(
        random.choice(GIVEN) for _ in range(random.randint(0, 1))
    )


def random_phone(prefix: str = "1") -> str:
    return "1" + random.choice("35789") + "".join(random.choice("0123456789") for _ in range(9))


def weighted_choice(pairs: List[Tuple[Any, int]]) -> Any:
    items = [p[0] for p in pairs]
    weights = [p[1] for p in pairs]
    return random.choices(items, weights=weights, k=1)[0]


def rand_dt(start: datetime, end: datetime) -> datetime:
    """在 [start, end) 内取一个随机时刻。"""
    delta = int((end - start).total_seconds())
    if delta <= 0:
        return start
    return start + timedelta(seconds=random.randint(0, delta - 1))


# =============================================================
# 生成步骤
# =============================================================

def reset_demo_data(conn) -> None:
    """清理上一次生成的数据（保留字典表 role/car_type 与计价规则）。"""
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in [
            "order_review", "order_track", "vehicle_location_current",
            "order_dispatch", "ride_order_status_log", "ride_order",
            "common_address", "vehicle", "user_session",
            "pricing_rule_backup_ignore",  # 占位，不存在则被忽略
        ]:
            try:
                cur.execute("DELETE FROM `%s`" % table)
            except Exception:
                pass
        for table in ["driver", "passenger"]:
            cur.execute("DELETE FROM `%s`" % table)
        cur.execute("DELETE FROM `account` WHERE `role_code` <> 'admin'")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    print("  [重置] 已清空业务数据（保留字典与管理端配置）")


def create_accounts(conn) -> Tuple[List[int], List[int]]:
    """批量创建乘客与司机账号及其档案，返回 (passenger_ids, driver_ids)。"""
    passengers: List[int] = []
    drivers: List[int] = []

    with conn.cursor() as cur:
        # ---- 乘客 ----
        for i in range(1, N_PASSENGERS + 1):
            salt, pwd_hash, iters = hash_password(DEMO_PASSWORD)
            cur.execute(
                "INSERT INTO `account`"
                " (`username`,`password_hash`,`salt`,`pwd_iterations`,`role_code`,`status`)"
                " VALUES (%s,%s,%s,%s,'passenger',1)",
                ("p%03d" % i, pwd_hash, salt, iters),
            )
            acc_id = cur.lastrowid
            cur.execute(
                "INSERT INTO `passenger`"
                " (`account_id`,`real_name`,`phone`,`gender`,`home_region`,`status`)"
                " VALUES (%s,%s,%s,%s,%s,1)",
                (acc_id, random_name(), random_phone(), random.choice([1, 1, 2, 2, 0]),
                 random.choice(REGIONS)),
            )
            passengers.append(cur.lastrowid)

        # ---- 司机 ----
        for i in range(1, N_DRIVERS + 1):
            salt, pwd_hash, iters = hash_password(DEMO_PASSWORD)
            cur.execute(
                "INSERT INTO `account`"
                " (`username`,`password_hash`,`salt`,`pwd_iterations`,`role_code`,`status`)"
                " VALUES (%s,%s,%s,%s,'driver',1)",
                ("d%03d" % i, pwd_hash, salt, iters),
            )
            acc_id = cur.lastrowid

            # 7 成司机在线，9 成审核通过；驾驶证到期日：少数临近到期，便于演示提醒
            online = 1 if random.random() < 0.7 else 0
            audit = 1 if random.random() < 0.9 else random.choice([0, 2])
            if random.random() < 0.15:
                license_expire = datetime.now().date() + timedelta(days=random.randint(-30, 25))
            else:
                license_expire = datetime.now().date() + timedelta(days=random.randint(60, 900))

            cur.execute(
                "INSERT INTO `driver`"
                " (`account_id`,`real_name`,`phone`,`id_card_no`,`driver_license_no`,"
                "  `license_expire_date`,`region`,`online_status`,`audit_status`,"
                "  `service_score`,`review_count`,`status`)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,5.00,0,1)",
                (acc_id, random_name(), random_phone(), None,
                 "DL%012d" % random.randint(1, 999999999999),
                 license_expire, random.choice(REGIONS), online, audit),
            )
            drivers.append(cur.lastrowid)

    conn.commit()
    print("  [账号] 乘客 %d 名、司机 %d 名" % (len(passengers), len(drivers)))
    return passengers, drivers


def create_vehicles(conn, drivers: List[int]) -> List[Dict[str, Any]]:
    """给司机配车（部分司机 2 辆），返回车辆信息列表。"""
    vehicles: List[Dict[str, Any]] = []
    used_plates = set()
    provinces = "京津冀晋蒙辽吉黑沪苏浙皖闽赣鲁豫鄂湘粤川贵云陕"

    with conn.cursor() as cur:
        for driver_id in drivers:
            n_cars = 2 if random.random() < 0.15 else 1
            for _ in range(n_cars):
                while True:
                    plate = "%s%s%s%05d" % (
                        random.choice(provinces), random.choice("ABCDEFGH"),
                        random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"),
                        random.randint(0, 99999),
                    )
                    if plate not in used_plates:
                        used_plates.add(plate)
                        break

                car_type_id = weighted_choice(list(zip(CAR_TYPE_IDS, CAR_TYPE_WEIGHTS)))
                brand, model = random.choice(BRANDS)
                seat = 6 if car_type_id == 3 else 4
                # 85% 可运营，其余为维修/注销
                r = random.random()
                operate = 1 if r < 0.85 else (0 if r < 0.95 else 2)

                insurance_expire = (
                    datetime.now().date() + timedelta(days=random.randint(-20, 25))
                    if random.random() < 0.15
                    else datetime.now().date() + timedelta(days=random.randint(60, 700))
                )
                annual_expire = (
                    datetime.now().date() + timedelta(days=random.randint(-20, 25))
                    if random.random() < 0.12
                    else datetime.now().date() + timedelta(days=random.randint(60, 700))
                )

                cur.execute(
                    "INSERT INTO `vehicle`"
                    " (`driver_id`,`car_type_id`,`plate_no`,`brand`,`model`,`color`,"
                    "  `seat_count`,`operate_status`,`vehicle_license_no`,`register_date`,"
                    "  `insurance_expire_date`,`annual_check_expire_date`)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (driver_id, car_type_id, plate, brand, model, random.choice(COLORS),
                     seat, operate, "VL%012d" % random.randint(1, 999999999999),
                     datetime.now().date() - timedelta(days=random.randint(90, 2000)),
                     insurance_expire, annual_expire),
                )
                vehicles.append({
                    "vehicle_id": cur.lastrowid,
                    "driver_id": driver_id,
                    "car_type_id": car_type_id,
                    "operate_status": operate,
                })

    conn.commit()
    print("  [车辆] 共 %d 辆（可运营 %d 辆）"
          % (len(vehicles), sum(1 for v in vehicles if v["operate_status"] == 1)))
    return vehicles


def create_addresses(conn, passengers: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """给乘客建常用地址。"""
    labels = ["家", "公司", "学校", "健身房", "父母家"]
    by_passenger: Dict[int, List[Dict[str, Any]]] = {}

    with conn.cursor() as cur:
        for pid in passengers:
            home_region = random.choice(REGIONS)
            n = random.randint(0, 3)
            chosen = random.sample(labels, n) if n else []
            by_passenger[pid] = []
            for label in chosen:
                region = home_region if random.random() < 0.6 else random.choice(REGIONS)
                name = random.choice(PLACE_TEMPLATES).format(region=region)
                lng = round(random.uniform(116.10, 116.70), 6)
                lat = round(random.uniform(39.70, 40.10), 6)
                cur.execute(
                    "INSERT INTO `common_address`"
                    " (`passenger_id`,`label`,`address_name`,`longitude`,`latitude`,`is_default`)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (pid, label, name, lng, lat, 1 if label == "家" else 0),
                )
                by_passenger[pid].append(
                    {"label": label, "name": name, "lng": lng, "lat": lat})

    conn.commit()
    print("  [地址] 常用地址 %d 条" % sum(len(v) for v in by_passenger.values()))
    return by_passenger


def load_pricing_rules(conn) -> Dict[int, Dict[str, Any]]:
    """读取各车型当前有效规则，用于订单价格快照。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `rule_id`,`car_type_id`,`version`,`base_price`,`base_distance_km`,"
            "       `per_km_price`,`per_min_price`,`min_price` FROM `v_pricing_current`"
        )
        rows = cur.fetchall()
    return {r["car_type_id"]: r for r in rows}


def create_orders(conn, passengers, drivers, vehicles, addresses, rules) -> Dict[str, Any]:
    """生成订单及其状态日志、派单记录、轨迹。

    in-flight 订单（accepted/arrived/in_progress）必须确保一个司机只有一单，
    否则会撞上 uk_active_driver 唯一索引。
    """
    now = datetime.now()
    window_start = now - timedelta(days=DAYS_BACK)

    # 只有「可运营车辆 + 审核通过 + 在线」的司机才可能被派单
    #
    # ⚠️ 必须带 ORDER BY。MySQL 不保证无 ORDER BY 的 GROUP BY 返回行序稳定，
    #    而下面对 eligible 做 random.choice / random.shuffle —— 行序一变，
    #    即使 random 的种子固定，选中的司机也会不同，生成的数据就不可复现了。
    #    （实测过：加 ORDER BY 之前，两次运行的订单状态分布会差 1~2 条。）
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.`driver_id`, d.`region` FROM `driver` d"
            " JOIN `vehicle` v ON v.`driver_id` = d.`driver_id` AND v.`operate_status` = 1"
            " WHERE d.`audit_status` = 1 AND d.`status` = 1"
            " GROUP BY d.`driver_id`, d.`region`"
            " ORDER BY d.`driver_id`"
        )
        eligible = cur.fetchall()

    if not eligible:
        raise RuntimeError("没有可派单的司机，请检查司机/车辆生成结果")

    # 司机 -> 该司机可运营车辆（按车型分组）
    veh_by_driver: Dict[int, List[Dict[str, Any]]] = {}
    for v in vehicles:
        if v["operate_status"] == 1:
            veh_by_driver.setdefault(v["driver_id"], []).append(v)

    # 预分配 in-flight 订单的司机，保证「一司一单」
    in_flight_plan: List[Dict[str, Any]] = []

    orders: List[Dict[str, Any]] = []
    logs: List[Tuple] = []
    dispatches: List[Tuple] = []
    tracks_raw: List[Dict[str, Any]] = []

    order_seq = 0

    # 先规划 in-flight 订单，避免司机冲突
    used_drivers_inflight = set()

    # ⚠️ 进行中订单必须限制在合理规模。
    #    这些订单的时间戳是「此刻往前几十分钟」，也就是**同时并发**在跑。
    #    如果按司机总数分配（曾出现过 24/30 名司机同时在载客），
    #    运力池会被吃空，v_available_drivers 直接返回 0 条 ——
    #    平台再也派不出单，这显然不符合真实情况。
    #    按运力池的 25% 封顶，既能让演示页面有「进行中」的订单可看，
    #    又给派单系统留出充足的空闲司机。
    inflight_cap = max(1, int(len(eligible) * 0.25))

    for _ in range(N_ORDERS):
        status = weighted_choice(STATUS_WEIGHTS)

        if status in ("accepted", "arrived", "in_progress"):
            candidates = [d for d in eligible if d["driver_id"] not in used_drivers_inflight]
            if not candidates or len(used_drivers_inflight) >= inflight_cap:
                # 达到并发上限或司机用完，退化成历史订单
                status = "completed"
            else:
                drv = random.choice(candidates)
                used_drivers_inflight.add(drv["driver_id"])
                in_flight_plan.append({"status": status, "driver": drv})
                continue

        orders.append({"status": status, "driver": None})

    # 把 in-flight 计划并回订单列表
    for plan in in_flight_plan:
        orders.append(plan)

    random.shuffle(orders)
    print("  [订单] 计划生成 %d 单（含 %d 单进行中类）" % (len(orders), len(in_flight_plan)))

    cur = conn.cursor()

    for idx, plan in enumerate(orders):
        order_seq += 1
        status = plan["status"]
        passenger_id = random.choice(passengers)

        # ---- 时间基线 ----
        if status in ("accepted", "arrived", "in_progress"):
            created_at = now - timedelta(minutes=random.randint(2, 90))
        else:
            created_at = rand_dt(window_start, now - timedelta(days=1))

        # ---- 起点 / 终点 ----
        if addresses[passenger_id] and random.random() < 0.5:
            addr = random.choice(addresses[passenger_id])
            origin_addr, o_lng, o_lat = addr["name"], addr["lng"], addr["lat"]
        else:
            region = random.choice(REGIONS)
            origin_addr = random.choice(PLACE_TEMPLATES).format(region=region)
            o_lng, o_lat = round(random.uniform(116.10, 116.70), 6), round(random.uniform(39.70, 40.10), 6)

        dest_region = random.choice(REGIONS)
        dest_addr = random.choice(PLACE_TEMPLATES).format(region=dest_region)
        d_lng, d_lat = round(random.uniform(116.10, 116.70), 6), round(random.uniform(39.70, 40.10), 6)

        origin_region = random.choice(REGIONS)
        car_type_id = weighted_choice(list(zip(CAR_TYPE_IDS, CAR_TYPE_WEIGHTS)))
        rule = rules.get(car_type_id) or rules[1]

        # ---- 里程与时长（按距离粗略估算，制造合理分布）----
        distance_km = round(random.uniform(1.5, 45.0), 3)
        duration_min = round(distance_km * random.uniform(1.8, 4.5) + random.uniform(2, 8), 2)

        # 实际里程/时长只有跑完的订单才有值，未完成的保持 NULL
        final_distance: Optional[float] = None
        final_duration: Optional[float] = None

        # ---- 状态时间线 ----
        accepted_at = arrived_at = started_at = completed_at = cancelled_at = None
        driver_id = vehicle_id = None
        cancelled_by = cancel_reason = None
        fare = None
        est_fare = None
        rule_id = rule["rule_id"]
        rule_version = rule["version"]

        t = created_at
        if status in ("accepted", "arrived", "in_progress", "completed"):
            t = t + timedelta(seconds=random.randint(15, 300))
            accepted_at = t
            drv = plan["driver"] or random.choice(eligible)
            driver_id = drv["driver_id"]
            pool = veh_by_driver.get(driver_id, [])
            same_type = [v for v in pool if v["car_type_id"] == car_type_id]
            vehicle_id = (same_type or pool or [{"vehicle_id": None}])[0]["vehicle_id"]

        if status in ("arrived", "in_progress", "completed"):
            t = t + timedelta(seconds=random.randint(120, 900))
            arrived_at = t

        if status in ("in_progress", "completed"):
            t = t + timedelta(seconds=random.randint(30, 180))
            started_at = t

        if status == "completed":
            t = t + timedelta(seconds=int(duration_min * 60))
            completed_at = t
            final_distance = distance_km
            final_duration = duration_min

        if status == "cancelled":
            t = t + timedelta(seconds=random.randint(20, 600))
            cancelled_at = t
            cancelled_by = random.choice(["passenger", "passenger", "driver", "system"])
            cancel_reason = random.choice([
                "乘客临时有事", "等待时间过长", "司机距离太远",
                "乘客已自行离开", "行程计划变更", "定位不准，无法会合",
            ])

        # ---- 预估价（下单时算一次）----
        cur.execute(
            "SELECT `fn_calc_fare`(%s,%s,%s,%s,%s,%s,%s) AS est",
            (rule["base_price"], rule["base_distance_km"], rule["per_km_price"],
             rule["per_min_price"], rule["min_price"], distance_km, duration_min),
        )
        est_fare = cur.fetchone()["est"]

        # ---- 最终车费（仅已完成）----
        if status == "completed":
            cur.execute(
                "SELECT `fn_calc_fare`(%s,%s,%s,%s,%s,%s,%s) AS fare",
                (rule["base_price"], rule["base_distance_km"], rule["per_km_price"],
                 rule["per_min_price"], rule["min_price"], distance_km, duration_min),
            )
            fare = cur.fetchone()["fare"]

        order_no = "RO%s%08d" % (created_at.strftime("%Y%m%d"), order_seq)

        cur.execute(
            "INSERT INTO `ride_order`"
            " (`order_no`,`passenger_id`,`driver_id`,`vehicle_id`,`car_type_id`,"
            "  `origin_address`,`origin_lng`,`origin_lat`,`origin_region`,"
            "  `dest_address`,`dest_lng`,`dest_lat`,`status`,"
            "  `created_at`,`accepted_at`,`arrived_at`,`started_at`,`completed_at`,"
            "  `cancelled_at`,`cancel_reason`,`cancelled_by`,"
            "  `pricing_rule_id`,`rule_version`,"
            "  `snap_base_price`,`snap_base_distance_km`,`snap_per_km_price`,"
            "  `snap_per_min_price`,`snap_min_price`,"
            "  `distance_km`,`duration_min`,`est_fare`,`fare_amount`)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "         %s,%s,%s,%s,%s,%s,%s,%s,"
            "         %s,%s,%s,%s,%s,%s,%s,"
            "         %s,%s,%s,%s)",
            (order_no, passenger_id, driver_id, vehicle_id, car_type_id,
             origin_addr, o_lng, o_lat, origin_region,
             dest_addr, d_lng, d_lat, status,
             created_at, accepted_at, arrived_at, started_at, completed_at,
             cancelled_at, cancel_reason, cancelled_by,
             rule_id, rule_version,
             rule["base_price"], rule["base_distance_km"], rule["per_km_price"],
             rule["per_min_price"], rule["min_price"],
             final_distance, final_duration,
             est_fare, fare),
        )
        order_id = cur.lastrowid

        # ---- 状态日志（按时间线逐条写入）----
        logs.append((order_id, None, "pending", "passenger", passenger_id, "乘客发起叫车", created_at))
        if accepted_at:
            logs.append((order_id, "pending", "accepted", "driver", driver_id, "司机接单", accepted_at))
        if arrived_at:
            logs.append((order_id, "accepted", "arrived", "driver", driver_id, "司机已到达上车点", arrived_at))
        if started_at:
            logs.append((order_id, "arrived", "in_progress", "driver", driver_id, "乘客已上车，行程开始", started_at))
        if completed_at:
            logs.append((order_id, "in_progress", "completed", "driver", driver_id,
                         "行程结束，里程 %.3f km，时长 %.2f 分钟" % (distance_km, duration_min), completed_at))
        if cancelled_at:
            logs.append((order_id, status if status != "cancelled" else "pending",
                         "cancelled", cancelled_by or "system", None, cancel_reason, cancelled_at))

        # ---- 派单记录：模拟 1~3 轮邀请 ----
        if driver_id or status == "cancelled":
            n_rounds = random.randint(1, 2)
            pool = [d for d in eligible if d["driver_id"] != driver_id]
            random.shuffle(pool)
            offset = 0
            for rnd in range(1, n_rounds + 1):
                batch = pool[offset:offset + random.randint(1, 3)]
                offset += len(batch)
                base_t = accepted_at or cancelled_at or created_at
                for d in batch:
                    st = "rejected" if random.random() < 0.25 else (
                        "expired" if random.random() < 0.3 else "cancelled")
                    dispatches.append((
                        order_id, d["driver_id"], st, rnd,
                        base_t - timedelta(seconds=random.randint(5, 120)),
                        base_t - timedelta(seconds=random.randint(0, 5)),
                        "模拟派单",
                    ))
            if driver_id:
                dispatches.append((
                    order_id, driver_id, "accepted", n_rounds,
                    accepted_at - timedelta(seconds=random.randint(5, 90)),
                    accepted_at, "司机接单",
                ))

        # ---- 轨迹：仅行程中/已完成，采样点控制数量 ----
        if status in ("in_progress", "completed") and started_at:
            n_points = random.randint(8, 25)
            end_t = completed_at or now
            span = (end_t - started_at).total_seconds()
            for k in range(n_points):
                frac = k / max(1, n_points - 1)
                pt_t = started_at + timedelta(seconds=span * frac)
                lng = o_lng + (d_lng - o_lng) * frac + random.uniform(-0.004, 0.004)
                lat = o_lat + (d_lat - o_lat) * frac + random.uniform(-0.004, 0.004)
                speed = round(random.uniform(5, 70), 2) if k not in (0, n_points - 1) else 0.0
                tracks_raw.append({
                    "order_id": order_id, "lng": round(lng, 6), "lat": round(lat, 6),
                    "speed": speed, "at": pt_t,
                })

    conn.commit()
    cur.close()

    # ---- 批量写入日志 / 派单 / 轨迹 ----
    with conn.cursor() as c:
        c.executemany(
            "INSERT INTO `ride_order_status_log`"
            " (`order_id`,`from_status`,`to_status`,`operator_type`,`operator_id`,`remark`,`changed_at`)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
            logs,
        )
        c.executemany(
            "INSERT INTO `order_dispatch`"
            " (`order_id`,`driver_id`,`status`,`batch_no`,`invited_at`,`responded_at`,`remark`)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
            dispatches,
        )
        c.executemany(
            "INSERT INTO `order_track` (`order_id`,`longitude`,`latitude`,`speed`,`located_at`)"
            " VALUES (%s,%s,%s,%s,%s)",
            [(t["order_id"], t["lng"], t["lat"], t["speed"], t["at"]) for t in tracks_raw],
        )
    conn.commit()

    print("  [日志] 状态变更日志 %d 条" % len(logs))
    print("  [派单] 派单邀请 %d 条" % len(dispatches))
    print("  [轨迹] 轨迹点 %d 个" % len(tracks_raw))

    return {"order_count": len(orders)}


def create_reviews(conn) -> int:
    """对已完成的订单按比例生成评价。触发器会自动维护司机服务分。"""
    with conn.cursor() as cur:
        # ⚠️ 不用 MySQL 的 ORDER BY RAND() 取随机样本 —— RAND() 是 MySQL 自己的
        #    伪随机数发生器，不受 Python 的 random.seed() 控制，会导致每次运行
        #    抽到不同的订单，破坏「固定种子可复现」。
        #    改为按 order_id 有序取出后，用 Python 的 random.sample 抽样。
        cur.execute(
            "SELECT `order_id`,`passenger_id`,`driver_id` FROM `ride_order`"
            " WHERE `status` = 'completed' ORDER BY `order_id`"
        )
        all_completed = cur.fetchall()

    completed = random.sample(all_completed, min(600, len(all_completed)))

    rows = []
    for o in completed:
        if random.random() > 0.6:      # 约 6 成订单被评价
            continue
        r = random.random()
        if r < 0.72:
            score = 5
            text = random.choice(REVIEW_TEXTS_GOOD)
        elif r < 0.90:
            score = 4
            text = random.choice(REVIEW_TEXTS_GOOD)
        elif r < 0.97:
            score = 3
            text = random.choice(REVIEW_TEXTS_MID)
        else:
            score = random.choice([1, 2])
            text = random.choice(REVIEW_TEXTS_BAD)

        tags = ",".join(random.sample(REVIEW_TAGS, random.randint(1, 3)))
        rows.append((o["order_id"], o["passenger_id"], o["driver_id"], score, text, tags))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO `order_review`"
            " (`order_id`,`passenger_id`,`driver_id`,`score`,`content`,`tags`,`created_at`)"
            " VALUES (%s,%s,%s,%s,%s,%s, NOW())",
            rows,
        )
    conn.commit()
    print("  [评价] 评价 %d 条（触发 trg_review_after_insert 维护司机服务分）" % len(rows))
    return len(rows)


def create_locations(conn, vehicles: List[Dict[str, Any]]) -> int:
    """给每辆可运营车辆写一条当前定位。"""
    rows = []
    now = datetime.now()
    for v in vehicles:
        if v["operate_status"] != 1:
            continue
        rows.append((
            v["vehicle_id"], v["driver_id"], None,
            round(random.uniform(116.10, 116.70), 6),
            round(random.uniform(39.70, 40.10), 6),
            round(random.uniform(0, 60), 2),
            round(random.uniform(0, 360), 2),
            now - timedelta(seconds=random.randint(1, 600)),
        ))

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO `vehicle_location_current`"
            " (`vehicle_id`,`driver_id`,`order_id`,`longitude`,`latitude`,"
            "  `speed`,`heading`,`located_at`)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            rows,
        )
    conn.commit()
    print("  [定位] 车辆当前定位 %d 条" % len(rows))
    return len(rows)


def print_summary(conn) -> None:
    """打印生成结果概览。"""
    print("\n" + "=" * 62)
    print("数据概览")
    print("=" * 62)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `status`, COUNT(*) AS n FROM `ride_order`"
            " GROUP BY `status` ORDER BY n DESC"
        )
        status_map = {"pending": "待接单", "accepted": "已接单", "arrived": "司机已到达",
                      "in_progress": "行程中", "completed": "已完成", "cancelled": "已取消"}
        for row in cur.fetchall():
            print("  %-10s %5d" % (status_map.get(row["status"], row["status"]), row["n"]))

        cur.execute("SELECT COUNT(*) AS n FROM `ride_order`")
        print("  %-10s %5d" % ("订单合计", cur.fetchone()["n"]))

        cur.execute("SELECT COUNT(*) AS n FROM `order_review`")
        print("  %-10s %5d" % ("评价", cur.fetchone()["n"]))

        cur.execute("SELECT COUNT(*) AS n FROM `driver` WHERE `review_count` > 0")
        print("  %-10s %5d" % ("有评价的司机", cur.fetchone()["n"]))

        cur.execute("SELECT COUNT(*) AS n FROM `v_expiring_licenses` WHERE `days_left` <= 30")
        print("  %-10s %5d" % ("30天内到期证照", cur.fetchone()["n"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="生成网约车系统演示数据")
    parser.add_argument("--reset", action="store_true",
                        help="生成前先清空既有业务数据（保留字典与管理端配置）")
    args = parser.parse_args()

    random.seed(RANDOM_SEED)

    print("=" * 62)
    print("演示数据生成器")
    print("数据库：%s" % DBConfig.describe())
    print("规模：乘客 %d / 司机 %d / 订单 %d / 跨度 %d 天"
          % (N_PASSENGERS, N_DRIVERS, N_ORDERS, DAYS_BACK))
    print("演示账号密码统一为：%s（如 p001 / d001）" % DEMO_PASSWORD)
    print("=" * 62)

    started = time.time()
    conn = connect()
    try:
        if args.reset:
            reset_demo_data(conn)

        passengers, drivers = create_accounts(conn)
        vehicles = create_vehicles(conn, drivers)
        addresses = create_addresses(conn, passengers)
        rules = load_pricing_rules(conn)
        create_orders(conn, passengers, drivers, vehicles, addresses, rules)
        create_reviews(conn)
        create_locations(conn, vehicles)
        print_summary(conn)
    finally:
        conn.close()

    print("\n完成，耗时 %.1f 秒" % (time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
