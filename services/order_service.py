# -*- coding: utf-8 -*-
"""订单业务编排：下单、派单、接单、行程推进、结算、取消。

【分层原则】
  * 涉及金额快照 / 并发抢占 / 结算的操作一律调用**存储过程**，
    由数据库保证原子性与并发正确性（见 sql/04_triggers_procedures.sql）。
  * 「到达 / 开始行程」这类简单状态推进，在应用层用一个事务包住
    「条件 UPDATE + 写状态日志」两步，避免中途失败导致状态与日志不一致。
  * 所有状态推进都带 `WHERE status = 期望值` 守卫，靠影响行数判断成败。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from db.connection import get_db, transaction
from repositories import dispatch_repo, order_repo, pricing_repo, vehicle_repo
from services.procedures import call_with_out

# 订单状态机：合法的前置状态
CANCELLABLE_STATUSES = ("pending", "accepted", "arrived")


# =============================================================
# 下单
# =============================================================

def create_order(
    passenger_id: int,
    car_type_id: int,
    origin_address: str,
    origin_lng: Optional[float],
    origin_lat: Optional[float],
    origin_region: str,
    dest_address: str,
    dest_lng: Optional[float],
    dest_lat: Optional[float],
    remark: Optional[str] = None,
    auto_dispatch: bool = True,
    dispatch_size: int = 3,
) -> Tuple[bool, Optional[int], str]:
    """乘客下单。调 sp_create_order 完成「取规则 → 价格快照 → 建单 → 写日志」。

    返回 (是否成功, 订单ID, 提示信息)。
    """
    if not origin_address or not dest_address:
        return False, None, "请填写上车点和下车点"
    if origin_address.strip() == dest_address.strip():
        return False, None, "上车点与下车点不能相同"

    with get_db() as conn:
        out = call_with_out(
            conn,
            "`sp_create_order`",
            (passenger_id, car_type_id, origin_address, origin_lng, origin_lat,
             origin_region, dest_address, dest_lng, dest_lat, remark),
            ["order_id", "order_no", "code", "msg"],
        )

        code = out.get("code")
        order_id = out.get("order_id")

        if code != "OK" or not order_id:
            return False, None, out.get("msg") or "下单失败"

        # 下单成功后立即派单，把订单推进到「有待应答邀请」的状态
        if auto_dispatch:
            call_with_out(
                conn,
                "`sp_dispatch_order`",
                (order_id, dispatch_size),
                ["batch_no", "invited", "code", "msg"],
            )

        return True, int(order_id), out.get("msg") or "下单成功"


def estimate_fare(car_type_id: int, distance_km: float, duration_min: float) -> Optional[float]:
    """下单前的预估价：直接调用数据库里的计价函数，保证与结算口径一致。"""
    with get_db() as conn:
        rule = pricing_repo.get_current_for_car_type(conn, car_type_id)
        if not rule:
            return None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `fn_calc_fare`(%s,%s,%s,%s,%s,%s,%s) AS `fare`",
                (rule["base_price"], rule["base_distance_km"], rule["per_km_price"],
                 rule["per_min_price"], rule["min_price"], distance_km, duration_min),
            )
            row = cur.fetchone()
            return float(row["fare"]) if row else None


# =============================================================
# 派单与接单
# =============================================================

def dispatch_order(order_id: int, batch_size: int = 3) -> Tuple[bool, str]:
    """对指定订单发起一轮派单。"""
    with get_db() as conn:
        out = call_with_out(
            conn,
            "`sp_dispatch_order`",
            (order_id, batch_size),
            ["batch_no", "invited", "code", "msg"],
        )
        ok = out.get("code") == "OK" and (out.get("invited") or 0) > 0
        return ok, out.get("msg") or ""


def accept_order(order_id: int, driver_id: int) -> Tuple[bool, str]:
    """司机接单。并发正确性由 sp_accept_order 保证。

    可能的结果码：
      OK               接单成功
      ALREADY_TAKEN    订单已被其他司机接走
      NOT_INVITED      未收到该订单的有效邀请
      NOT_QUALIFIED    司机资格失效或名下无可运营车辆
      DRIVER_BUSY      该司机已有进行中的订单（唯一索引兜底）
    """
    with get_db() as conn:
        out = call_with_out(
            conn,
            "`sp_accept_order`",
            (order_id, driver_id),
            ["vehicle_id", "code", "msg"],
        )
        return out.get("code") == "OK", out.get("msg") or "接单失败"


def reject_order(order_id: int, driver_id: int, reason: Optional[str] = None) -> Tuple[bool, str]:
    """司机拒单。订单状态不变，只是这条邀请被标记为 rejected。"""
    with get_db() as conn:
        affected = dispatch_repo.reject(conn, order_id, driver_id, reason or "司机拒单")
        if affected == 0:
            return False, "该邀请已失效，无法拒单"
        return True, "已拒绝该订单"


# =============================================================
# 行程推进（到达 / 开始）
# =============================================================

def _advance_status(order_id: int, driver_id: int, from_status: str, to_status: str,
                    extra_sql: str, remark: str) -> Tuple[bool, str]:
    """通用状态推进：一个事务内完成「条件更新 + 写日志」。

    条件更新带回滚守卫：若影响行数为 0，说明状态已被其他操作改变，
    此时连同日志一起回滚，不会留下自相矛盾的数据。
    """
    with get_db() as conn:
        with transaction(conn) as cur:
            cur.execute(
                "UPDATE `ride_order`"
                "   SET `status` = %s, " + extra_sql +
                " WHERE `order_id` = %s AND `driver_id` = %s AND `status` = %s",
                (to_status, order_id, driver_id, from_status),
            )
            if cur.rowcount == 0:
                # 主动抛异常触发 rollback；不写日志
                raise ValueError(
                    "订单当前状态不允许执行该操作（可能已被其他操作改变，请刷新后重试）"
                )

            cur.execute(
                "INSERT INTO `ride_order_status_log`"
                " (`order_id`,`from_status`,`to_status`,`operator_type`,`operator_id`,`remark`)"
                " VALUES (%s,%s,%s,'driver',%s,%s)",
                (order_id, from_status, to_status, driver_id, remark),
            )
    return True, remark


def arrive_order(order_id: int, driver_id: int) -> Tuple[bool, str]:
    """司机到达上车点：accepted -> arrived。"""
    try:
        return _advance_status(order_id, driver_id, "accepted", "arrived",
                               "`arrived_at` = NOW()", "司机已到达上车点")
    except ValueError as exc:
        return False, str(exc)


def start_order(order_id: int, driver_id: int) -> Tuple[bool, str]:
    """乘客上车、行程开始：arrived -> in_progress。"""
    try:
        return _advance_status(order_id, driver_id, "arrived", "in_progress",
                               "`started_at` = NOW()", "乘客已上车，行程开始")
    except ValueError as exc:
        return False, str(exc)


# =============================================================
# 结算与取消
# =============================================================

def complete_order(order_id: int, driver_id: int, distance_km: float,
                   duration_min: float) -> Tuple[bool, Optional[float], str]:
    """结束行程并按订单快照列结算。返回 (是否成功, 车费, 提示)。"""
    if distance_km is None or distance_km < 0:
        return False, None, "请填写正确的行驶里程"
    if duration_min is None or duration_min <= 0:
        return False, None, "请填写正确的行程时长"

    with get_db() as conn:
        out = call_with_out(
            conn,
            "`sp_complete_order`",
            (order_id, driver_id, distance_km, duration_min),
            ["fare_amount", "code", "msg"],
        )
        fare = out.get("fare_amount")
        return (
            out.get("code") == "OK",
            float(fare) if fare is not None else None,
            out.get("msg") or "",
        )


def cancel_order(order_id: int, operator_type: str, operator_id: Optional[int],
                 reason: Optional[str] = None) -> Tuple[bool, str]:
    """取消订单。可取消的状态：待接单 / 已接单 / 司机已到达。"""
    with get_db() as conn:
        out = call_with_out(
            conn,
            "`sp_cancel_order`",
            (order_id, operator_type, operator_id, reason or "用户取消"),
            ["code", "msg"],
        )
        return out.get("code") == "OK", out.get("msg") or "取消失败"


def expire_stale_dispatch() -> int:
    """把已超时未应答的派单邀请批量置为 expired。可由定时任务或管理端触发。"""
    with get_db() as conn:
        out = call_with_out(conn, "`sp_expire_dispatch`", (), ["expired"])
        return int(out.get("expired") or 0)


# =============================================================
# 查询辅助
# =============================================================

def can_review(order_id: int, passenger_id: int) -> Tuple[bool, str]:
    """判断某订单能否被评价：必须是自己已完成、且尚未评价过的订单。"""
    from repositories import review_repo

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)
        if not order:
            return False, "订单不存在"
        if order["passenger_id"] != passenger_id:
            return False, "无权评价该订单"
        if order["status"] != "completed":
            return False, "只有已完成的订单才能评价"
        if review_repo.get_by_order(conn, order_id):
            return False, "该订单已评价过"
    return True, ""


def submit_review(order_id: int, passenger_id: int, score: int,
                  content: Optional[str], tags: Optional[str]) -> Tuple[bool, str]:
    """提交评价。司机服务分由触发器 trg_review_after_insert 自动维护。"""
    from repositories import review_repo

    ok, msg = can_review(order_id, passenger_id)
    if not ok:
        return False, msg
    if score not in (1, 2, 3, 4, 5):
        return False, "评分必须在 1~5 之间"

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)
        review_repo.create(conn, order_id, passenger_id, order["driver_id"],
                           score, content, tags)
    return True, "评价成功，感谢您的反馈"
