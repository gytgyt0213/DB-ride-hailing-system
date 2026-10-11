# -*- coding: utf-8 -*-
"""管理端路由：司机/车辆/乘客/计价规则管理、订单监控、统计报表。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from repositories import (account_repo, address_repo, dispatch_repo, driver_repo,
                          order_repo, passenger_repo, pricing_repo, review_repo,
                          stats_repo, vehicle_repo)
from routers.deps import base_context, redirect_with_message, require_role, templates
from security import session
from services import auth_service, order_service

router = APIRouter(prefix="/admin", tags=["管理端"])

REGIONS = ["朝阳区", "海淀区", "东城区", "西城区",
           "丰台区", "通州区", "昌平区", "大兴区"]

STATUS_LABELS = {
    "pending": "待接单", "accepted": "已接单", "arrived": "司机已到达",
    "in_progress": "行程中", "completed": "已完成", "cancelled": "已取消",
}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_role(request, "admin")

    with get_db() as conn:
        overview = stats_repo.overview(conn)
        by_status = stats_repo.orders_by_status(conn)
        by_hour = stats_repo.orders_by_hour(conn)
        stages = stats_repo.stage_durations(conn)
        reviews = stats_repo.review_summary(conn)
        expiring = stats_repo.expiring_licenses(conn, days=30)
        recent_orders = order_repo.list_all(conn, limit=8)

    return templates.TemplateResponse(
        "admin/dashboard.html",
        base_context(request, user=user, overview=overview, by_status=by_status,
                     by_hour=by_hour, stages=stages, reviews=reviews,
                     expiring=expiring, recent_orders=recent_orders,
                     status_labels=STATUS_LABELS),
    )


# -------------------------------------------------------------
# 司机管理
# -------------------------------------------------------------

@router.get("/drivers", response_class=HTMLResponse)
def driver_list(request: Request, keyword: str = "", audit_status: str = ""):
    user = require_role(request, "admin")
    audit = int(audit_status) if audit_status.strip().isdigit() else None

    with get_db() as conn:
        drivers = driver_repo.list_all(conn, keyword=keyword.strip() or None,
                                       audit_status=audit)
        pending_count = len(driver_repo.list_all(conn, audit_status=0))

    return templates.TemplateResponse(
        "admin/driver_list.html",
        base_context(request, user=user, drivers=drivers, keyword=keyword,
                     audit_status=audit_status, pending_count=pending_count,
                     regions=REGIONS),
    )


@router.post("/drivers/new")
def driver_create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    real_name: str = Form(""),
    phone: str = Form(""),
    region: str = Form(""),
    id_card_no: str = Form(""),
    driver_license_no: str = Form(""),
    license_expire_date: str = Form(""),
    audit_status: int = Form(1),
):
    user = require_role(request, "admin")

    account_id, message = auth_service.create_account_for_role(username, password, "driver")
    if account_id is None:
        raise redirect_with_message("/admin/drivers", message, "error")

    with get_db() as conn:
        try:
            driver_repo.create(
                conn, account_id, real_name.strip() or username, phone.strip(),
                region or REGIONS[0], id_card_no.strip() or None,
                driver_license_no.strip() or None,
                license_expire_date.strip() or None, audit_status)
        except Exception:
            # 档案创建失败时把账号一并清理，避免留下孤立账号
            account_repo.set_status(conn, account_id, 0)
            raise redirect_with_message(
                "/admin/drivers",
                "司机档案创建失败（身份证号可能重复），账号已禁用", "error")

    raise redirect_with_message("/admin/drivers", "司机账号已创建", "success")


@router.post("/drivers/{driver_id}/audit")
def driver_audit(request: Request, driver_id: int, audit_status: int = Form(1)):
    require_role(request, "admin")
    with get_db() as conn:
        driver_repo.set_audit_status(conn, driver_id, audit_status)

    label = {0: "已置为待审核", 1: "审核通过", 2: "已驳回并强制下线"}.get(audit_status, "已更新")
    raise redirect_with_message("/admin/drivers", label, "success")


@router.post("/drivers/{driver_id}/status")
def driver_status(request: Request, driver_id: int, status: int = Form(1)):
    require_role(request, "admin")
    with get_db() as conn:
        driver_repo.set_status(conn, driver_id, status)

    raise redirect_with_message(
        "/admin/drivers", "司机已启用" if status == 1 else "司机已停用并下线", "success")


# -------------------------------------------------------------
# 车辆管理
# -------------------------------------------------------------

@router.get("/vehicles", response_class=HTMLResponse)
def vehicle_list(request: Request, keyword: str = "", operate_status: str = ""):
    user = require_role(request, "admin")
    op = int(operate_status) if operate_status.strip().isdigit() else None

    with get_db() as conn:
        vehicles = vehicle_repo.list_all(conn, keyword=keyword.strip() or None,
                                         operate_status=op)
        car_types = vehicle_repo.list_car_types(conn)
        drivers = driver_repo.list_all(conn, limit=500)

    return templates.TemplateResponse(
        "admin/vehicle_list.html",
        base_context(request, user=user, vehicles=vehicles, car_types=car_types,
                     drivers=drivers, keyword=keyword,
                     operate_status=operate_status),
    )


@router.post("/vehicles/new")
def vehicle_create(
    request: Request,
    driver_id: int = Form(0),
    car_type_id: int = Form(1),
    plate_no: str = Form(""),
    brand: str = Form(""),
    model: str = Form(""),
    color: str = Form(""),
    seat_count: int = Form(4),
    operate_status: int = Form(1),
    vehicle_license_no: str = Form(""),
    insurance_expire_date: str = Form(""),
    annual_check_expire_date: str = Form(""),
):
    require_role(request, "admin")
    plate = plate_no.strip().upper()

    if not driver_id:
        raise redirect_with_message("/admin/vehicles", "请选择所属司机", "error")
    if not plate:
        raise redirect_with_message("/admin/vehicles", "请填写车牌号", "error")

    with get_db() as conn:
        if vehicle_repo.plate_exists(conn, plate):
            raise redirect_with_message("/admin/vehicles", "该车牌号已存在", "error")
        try:
            vehicle_repo.create(
                conn, driver_id, car_type_id, plate,
                brand.strip() or None, model.strip() or None, color.strip() or None,
                seat_count, operate_status, vehicle_license_no.strip() or None, None,
                insurance_expire_date.strip() or None,
                annual_check_expire_date.strip() or None)
        except Exception:
            raise redirect_with_message("/admin/vehicles", "车辆信息有误，请检查后重试", "error")

    raise redirect_with_message("/admin/vehicles", "车辆已添加", "success")


@router.post("/vehicles/{vehicle_id}/status")
def vehicle_status(request: Request, vehicle_id: int, operate_status: int = Form(1)):
    require_role(request, "admin")
    with get_db() as conn:
        vehicle_repo.set_operate_status(conn, vehicle_id, operate_status)

    label = {0: "已置为不可运营", 1: "已置为可运营", 2: "已注销"}.get(operate_status, "已更新")
    raise redirect_with_message("/admin/vehicles", label, "success")


# -------------------------------------------------------------
# 计价规则管理（版本化）
# -------------------------------------------------------------

@router.get("/pricing", response_class=HTMLResponse)
def pricing_list(request: Request):
    user = require_role(request, "admin")
    with get_db() as conn:
        current = pricing_repo.list_current(conn)
        all_rules = pricing_repo.list_all(conn)
        car_types = vehicle_repo.list_car_types(conn)

    return templates.TemplateResponse(
        "admin/pricing_list.html",
        base_context(request, user=user, current=current,
                     all_rules=all_rules, car_types=car_types),
    )


@router.post("/pricing/new")
def pricing_new(
    request: Request,
    car_type_id: int = Form(1),
    rule_name: str = Form(""),
    base_price: str = Form("0"),
    base_distance_km: str = Form("3"),
    per_km_price: str = Form("0"),
    per_min_price: str = Form("0"),
    min_price: str = Form("0"),
):
    user = require_role(request, "admin")

    try:
        values = (float(base_price), float(base_distance_km), float(per_km_price),
                  float(per_min_price), float(min_price))
    except (TypeError, ValueError):
        raise redirect_with_message("/admin/pricing", "价格参数必须是数字", "error")

    if any(v < 0 for v in values):
        raise redirect_with_message("/admin/pricing", "价格参数不能为负数", "error")

    with get_db() as conn:
        result = pricing_repo.create_new_version(
            conn, car_type_id, rule_name.strip() or "新版本",
            values[0], values[1], values[2], values[3], values[4],
            user["profile_id"])

    raise redirect_with_message(
        "/admin/pricing",
        "已发布第 %d 版规则，同时关闭 %d 条旧规则" % (result["version"], result["closed"]),
        "success")


@router.post("/pricing/{rule_id}/toggle")
def pricing_toggle(request: Request, rule_id: int, status: int = Form(1)):
    require_role(request, "admin")
    with get_db() as conn:
        pricing_repo.toggle_status(conn, rule_id, status)

    raise redirect_with_message("/admin/pricing", "规则状态已更新", "success")


# -------------------------------------------------------------
# 乘客管理
# -------------------------------------------------------------

@router.get("/passengers", response_class=HTMLResponse)
def passenger_list(request: Request, keyword: str = ""):
    user = require_role(request, "admin")
    with get_db() as conn:
        passengers = passenger_repo.list_all(conn, keyword=keyword.strip() or None)

    return templates.TemplateResponse(
        "admin/passenger_list.html",
        base_context(request, user=user, passengers=passengers, keyword=keyword),
    )


@router.post("/passengers/{passenger_id}/status")
def passenger_status(request: Request, passenger_id: int, status: int = Form(1)):
    require_role(request, "admin")
    with get_db() as conn:
        p = passenger_repo.get_by_id(conn, passenger_id)
        if not p:
            raise redirect_with_message("/admin/passengers", "乘客不存在", "error")
        account_repo.set_status(conn, p["account_id"], status)

    raise redirect_with_message(
        "/admin/passengers", "乘客已启用" if status == 1 else "乘客账号已禁用", "success")


# -------------------------------------------------------------
# 订单监控
# -------------------------------------------------------------

@router.get("/orders", response_class=HTMLResponse)
def order_monitor(request: Request, status: str = "", keyword: str = "",
                  region: str = ""):
    user = require_role(request, "admin")

    with get_db() as conn:
        orders = order_repo.list_all(conn, status=status.strip() or None,
                                     keyword=keyword.strip() or None,
                                     region=region.strip() or None, limit=200)
        counts = stats_repo.orders_by_status(conn)

    by_status = {row["status"]: row["n"] for row in counts}

    return templates.TemplateResponse(
        "admin/order_list.html",
        base_context(request, user=user, orders=orders, by_status=by_status,
                     current_status=status, keyword=keyword, region=region,
                     regions=REGIONS, status_labels=STATUS_LABELS),
    )


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: int):
    user = require_role(request, "admin")

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)
        if not order:
            raise redirect_with_message("/admin/orders", "订单不存在", "error")

        logs = order_repo.get_status_log(conn, order_id)
        duration = order_repo.get_duration(conn, order_id)
        dispatches = dispatch_repo.list_order_dispatches(conn, order_id)
        track_summary = order_repo.get_track_summary(conn, order_id)
        review = review_repo.get_by_order(conn, order_id)

    return templates.TemplateResponse(
        "admin/order_detail.html",
        base_context(request, user=user, order=order, logs=logs,
                     duration=duration, dispatches=dispatches,
                     track_summary=track_summary, review=review,
                     status_labels=STATUS_LABELS),
    )


@router.post("/orders/{order_id}/dispatch")
def order_dispatch(request: Request, order_id: int, batch_size: int = Form(3)):
    require_role(request, "admin")
    ok, message = order_service.dispatch_order(order_id, batch_size)

    raise redirect_with_message(
        "/admin/orders/%d" % order_id, message, "success" if ok else "warning")


@router.post("/orders/{order_id}/cancel")
def order_cancel(request: Request, order_id: int, reason: str = Form("")):
    user = require_role(request, "admin")
    ok, message = order_service.cancel_order(
        order_id, "admin", user["profile_id"], reason.strip() or "管理员取消")

    raise redirect_with_message(
        "/admin/orders/%d" % order_id, message, "success" if ok else "error")


# -------------------------------------------------------------
# 统计报表与选做功能
# -------------------------------------------------------------

@router.get("/stats", response_class=HTMLResponse)
def stats(request: Request):
    user = require_role(request, "admin")

    with get_db() as conn:
        overview = stats_repo.overview(conn)
        by_status = stats_repo.orders_by_status(conn)
        by_car_type = stats_repo.orders_by_car_type(conn)
        by_hour = stats_repo.orders_by_hour(conn)
        by_date = stats_repo.orders_by_date(conn, days=30)
        region_orders = stats_repo.region_orders(conn)
        region_sd = stats_repo.region_supply_demand(conn, days=7)
        top_drivers = stats_repo.top_drivers(conn, limit=10)
        dispatch_stats = stats_repo.dispatch_stats(conn)
        stages = stats_repo.stage_durations(conn)
        reviews = stats_repo.review_summary(conn)
        score_dist = review_repo.score_distribution(conn)
        data_overview = stats_repo.data_overview(conn)

    return templates.TemplateResponse(
        "admin/stats.html",
        base_context(
            request, user=user, overview=overview, by_status=by_status,
            by_car_type=by_car_type, by_hour=by_hour, by_date=by_date,
            region_orders=region_orders, region_sd=region_sd,
            top_drivers=top_drivers, dispatch_stats=dispatch_stats,
            stages=stages, reviews=reviews, score_dist=score_dist,
            data_overview=data_overview, status_labels=STATUS_LABELS,
        ),
    )


@router.get("/regions", response_class=HTMLResponse)
def regions(request: Request, days: int = 7):
    """选做：区域供需统计。"""
    user = require_role(request, "admin")
    with get_db() as conn:
        supply_demand = stats_repo.region_supply_demand(conn, days=days)
        region_orders = stats_repo.region_orders(conn)

    return templates.TemplateResponse(
        "admin/region_stats.html",
        base_context(request, user=user, supply_demand=supply_demand,
                     region_orders=region_orders, days=days),
    )


@router.get("/licenses", response_class=HTMLResponse)
def licenses(request: Request, days: int = 30):
    """选做：证照与保险到期提醒。"""
    user = require_role(request, "admin")
    with get_db() as conn:
        items = stats_repo.expiring_licenses(conn, days=days)

    return templates.TemplateResponse(
        "admin/licenses.html",
        base_context(request, user=user, items=items, days=days),
    )


@router.get("/reviews", response_class=HTMLResponse)
def reviews(request: Request):
    user = require_role(request, "admin")
    with get_db() as conn:
        recent = review_repo.list_recent(conn, limit=100)
        scores = review_repo.driver_scores(conn, min_reviews=1, limit=50)
        dist = review_repo.score_distribution(conn)
        summary = stats_repo.review_summary(conn)

    return templates.TemplateResponse(
        "admin/reviews.html",
        base_context(request, user=user, recent=recent, scores=scores,
                     score_dist=dist, summary=summary),
    )


@router.get("/maintenance", response_class=HTMLResponse)
def maintenance_page(request: Request):
    """数据库维护：清理过期会话与过期派单邀请。"""
    user = require_role(request, "admin")
    with get_db() as conn:
        dispatch_stats = dispatch_repo.stats_by_status(conn)

    return templates.TemplateResponse(
        "admin/maintenance.html",
        base_context(request, user=user, dispatch_stats=dispatch_stats),
    )


@router.post("/maintenance/run")
def maintenance_run(request: Request):
    require_role(request, "admin")

    with get_db() as conn:
        expired_sessions = session.cleanup_expired(conn)

    expired_dispatch = order_service.expire_stale_dispatch()

    raise redirect_with_message(
        "/admin/maintenance",
        "已清理过期会话 %d 条、过期派单邀请 %d 条" % (expired_sessions, expired_dispatch),
        "success")
