# -*- coding: utf-8 -*-
"""司机端路由：上下线、接单/拒单、行程推进、车辆管理。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from repositories import (dispatch_repo, driver_repo, order_repo, review_repo,
                          stats_repo, vehicle_repo)
from routers.deps import base_context, redirect_with_message, require_role, templates
from services import order_service

router = APIRouter(prefix="/driver", tags=["司机端"])

REGIONS = ["朝阳区", "海淀区", "东城区", "西城区",
           "丰台区", "通州区", "昌平区", "大兴区"]


def _drv(user) -> int:
    return int(user["profile_id"])


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = require_role(request, "driver")
    did = _drv(user)

    with get_db() as conn:
        profile = driver_repo.get_by_id(conn, did)
        active = order_repo.get_driver_active_order(conn, did)
        invites = dispatch_repo.list_driver_invites(conn, did, status="invited", limit=20)
        pending_count = dispatch_repo.count_pending_invites(conn, did)
        recent = order_repo.list_by_driver(conn, did, limit=5)
        vehicles = vehicle_repo.list_by_driver(conn, did)

        completed = order_repo.list_by_driver(conn, did, statuses=["completed"], limit=1000)
        revenue = sum(float(o["fare_amount"] or 0) for o in completed)

    # 过滤掉已过期或者订单状态已变的邀请，页面只展示仍可接的
    open_invites = [i for i in invites
                    if i["order_status"] == "pending"
                    and i["invite_status"] == "invited"]

    return templates.TemplateResponse(
        "driver/home.html",
        base_context(
            request, user=user, profile=profile, active_order=active,
            invites=open_invites, pending_count=pending_count,
            recent_orders=recent, vehicles=vehicles,
            stats={"completed": len(completed), "revenue": round(revenue, 2)},
        ),
    )


@router.post("/online")
def go_online(request: Request):
    user = require_role(request, "driver")
    did = _drv(user)

    with get_db() as conn:
        profile = driver_repo.get_by_id(conn, did)
        if profile["audit_status"] != 1:
            raise redirect_with_message("/driver/", "资质未通过审核，无法上线接单", "error")

        available = [v for v in vehicle_repo.list_by_driver(conn, did)
                     if v["operate_status"] == 1]
        if not available:
            raise redirect_with_message(
                "/driver/", "名下没有可运营的车辆，无法上线接单", "error")

        driver_repo.set_online(conn, did, 1)

    raise redirect_with_message("/driver/", "已上线，开始接收订单", "success")


@router.post("/offline")
def go_offline(request: Request):
    user = require_role(request, "driver")
    did = _drv(user)

    with get_db() as conn:
        active = order_repo.get_driver_active_order(conn, did)
        if active:
            raise redirect_with_message(
                "/driver/", "有进行中的订单，结束后才能下线", "error")
        driver_repo.set_online(conn, did, 0)

    raise redirect_with_message("/driver/", "已下线", "info")


@router.get("/pool", response_class=HTMLResponse)
def pool(request: Request):
    """待接单池：展示发给我的邀请。"""
    user = require_role(request, "driver")
    did = _drv(user)

    with get_db() as conn:
        invites = dispatch_repo.list_driver_invites(conn, did, status="invited", limit=50)

    open_invites = [i for i in invites if i["order_status"] == "pending"]

    return templates.TemplateResponse(
        "driver/pool.html",
        base_context(request, user=user, invites=open_invites),
    )


@router.post("/orders/{order_id}/accept")
def accept(request: Request, order_id: int):
    user = require_role(request, "driver")
    ok, message = order_service.accept_order(order_id, _drv(user))

    raise redirect_with_message(
        "/driver/orders/%d" % order_id if ok else "/driver/pool",
        message, "success" if ok else "error")


@router.post("/orders/{order_id}/reject")
def reject(request: Request, order_id: int, reason: str = Form("")):
    user = require_role(request, "driver")
    ok, message = order_service.reject_order(
        order_id, _drv(user), reason.strip() or "司机拒单")

    raise redirect_with_message(
        "/driver/pool", message, "success" if ok else "error")


@router.get("/orders", response_class=HTMLResponse)
def order_list(request: Request, status: str = ""):
    user = require_role(request, "driver")
    did = _drv(user)

    status = status.strip()
    statuses = [status] if status else None

    with get_db() as conn:
        orders = order_repo.list_by_driver(conn, did, statuses=statuses, limit=100)
        all_orders = order_repo.list_by_driver(conn, did, limit=1000)

    by_status = {}
    for o in all_orders:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1

    return templates.TemplateResponse(
        "driver/order_list.html",
        base_context(request, user=user, orders=orders,
                     current_status=status, by_status=by_status,
                     total=len(all_orders)),
    )


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: int):
    user = require_role(request, "driver")
    did = _drv(user)

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)
        if not order or order["driver_id"] != did:
            raise redirect_with_message("/driver/orders", "订单不存在", "error")

        logs = order_repo.get_status_log(conn, order_id)
        duration = order_repo.get_duration(conn, order_id)
        track_summary = order_repo.get_track_summary(conn, order_id)
        points = order_repo.get_track(conn, order_id)
        review = review_repo.get_by_order(conn, order_id)

    return templates.TemplateResponse(
        "driver/order_detail.html",
        base_context(request, user=user, order=order, logs=logs,
                     duration=duration, track_summary=track_summary,
                     points=points, review=review),
    )


@router.post("/orders/{order_id}/arrive")
def arrive(request: Request, order_id: int):
    user = require_role(request, "driver")
    ok, message = order_service.arrive_order(order_id, _drv(user))
    raise redirect_with_message(
        "/driver/orders/%d" % order_id, message, "success" if ok else "error")


@router.post("/orders/{order_id}/start")
def start(request: Request, order_id: int):
    user = require_role(request, "driver")
    ok, message = order_service.start_order(order_id, _drv(user))
    raise redirect_with_message(
        "/driver/orders/%d" % order_id, message, "success" if ok else "error")


@router.post("/orders/{order_id}/complete")
def complete(
    request: Request,
    order_id: int,
    distance_km: str = Form(""),
    duration_min: str = Form(""),
):
    user = require_role(request, "driver")

    try:
        dist = float(distance_km)
        dura = float(duration_min)
    except (TypeError, ValueError):
        raise redirect_with_message(
            "/driver/orders/%d" % order_id, "请填写正确的里程和时长", "error")

    ok, fare, message = order_service.complete_order(order_id, _drv(user), dist, dura)

    raise redirect_with_message(
        "/driver/orders/%d" % order_id, message, "success" if ok else "error")


# -------------------------------------------------------------
# 车辆管理
# -------------------------------------------------------------

@router.get("/vehicles", response_class=HTMLResponse)
def vehicle_list(request: Request):
    user = require_role(request, "driver")
    with get_db() as conn:
        vehicles = vehicle_repo.list_by_driver(conn, _drv(user))
        car_types = vehicle_repo.list_car_types(conn)

    return templates.TemplateResponse(
        "driver/vehicle_list.html",
        base_context(request, user=user, vehicles=vehicles,
                     car_types=car_types, regions=REGIONS),
    )


@router.post("/vehicles/new")
def vehicle_create(
    request: Request,
    car_type_id: int = Form(1),
    plate_no: str = Form(""),
    brand: str = Form(""),
    model: str = Form(""),
    color: str = Form(""),
    seat_count: int = Form(4),
    vehicle_license_no: str = Form(""),
    insurance_expire_date: str = Form(""),
    annual_check_expire_date: str = Form(""),
):
    user = require_role(request, "driver")
    plate = plate_no.strip().upper()

    if not plate:
        raise redirect_with_message("/driver/vehicles", "请填写车牌号", "error")

    with get_db() as conn:
        if vehicle_repo.plate_exists(conn, plate):
            raise redirect_with_message("/driver/vehicles", "该车牌号已存在", "error")
        try:
            vehicle_repo.create(
                conn, _drv(user), car_type_id, plate,
                brand.strip() or None, model.strip() or None, color.strip() or None,
                seat_count, 1, vehicle_license_no.strip() or None, None,
                insurance_expire_date.strip() or None,
                annual_check_expire_date.strip() or None)
        except Exception:
            raise redirect_with_message("/driver/vehicles", "车辆信息有误，请检查后重试", "error")

    raise redirect_with_message("/driver/vehicles", "车辆已添加", "success")


@router.post("/vehicles/{vehicle_id}/status")
def vehicle_status(request: Request, vehicle_id: int, operate_status: int = Form(1)):
    user = require_role(request, "driver")

    with get_db() as conn:
        vehicle = vehicle_repo.get_by_id(conn, vehicle_id)
        if not vehicle or vehicle["driver_id"] != _drv(user):
            raise redirect_with_message("/driver/vehicles", "车辆不存在", "error")
        vehicle_repo.set_operate_status(conn, vehicle_id, operate_status)

    label = {0: "已置为不可运营", 1: "已置为可运营", 2: "已注销"}.get(operate_status, "状态已更新")
    raise redirect_with_message("/driver/vehicles", label, "success")


# -------------------------------------------------------------
# 评价与个人资料
# -------------------------------------------------------------

@router.get("/reviews", response_class=HTMLResponse)
def reviews(request: Request):
    user = require_role(request, "driver")
    with get_db() as conn:
        items = review_repo.list_by_driver(conn, _drv(user), limit=100)
        profile = driver_repo.get_by_id(conn, _drv(user))

    return templates.TemplateResponse(
        "driver/reviews.html",
        base_context(request, user=user, reviews=items, profile=profile),
    )


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    user = require_role(request, "driver")
    with get_db() as conn:
        profile = driver_repo.get_by_id(conn, _drv(user))

    return templates.TemplateResponse(
        "driver/profile.html",
        base_context(request, user=user, profile=profile, regions=REGIONS),
    )


@router.post("/profile")
def profile_update(
    request: Request,
    real_name: str = Form(""),
    phone: str = Form(""),
    region: str = Form(""),
    id_card_no: str = Form(""),
    driver_license_no: str = Form(""),
    license_expire_date: str = Form(""),
):
    user = require_role(request, "driver")

    with get_db() as conn:
        driver_repo.update_profile(
            conn, _drv(user), real_name.strip(), phone.strip(), region,
            id_card_no.strip() or None, driver_license_no.strip() or None,
            license_expire_date.strip() or None)

    raise redirect_with_message("/driver/profile", "资料已更新", "success")
