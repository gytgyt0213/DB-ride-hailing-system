# -*- coding: utf-8 -*-
"""乘客端路由：下单、订单追踪、取消、评价、常用地址。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.connection import get_db
from repositories import address_repo, dispatch_repo, order_repo, pricing_repo, vehicle_repo
from routers.deps import base_context, redirect_with_message, require_role, templates
from services import order_service

router = APIRouter(prefix="/passenger", tags=["乘客端"])

# 演示用的城市区域（派单按区域匹配，因此下单时需选择一个区域）
REGIONS = ["朝阳区", "海淀区", "东城区", "西城区",
           "丰台区", "通州区", "昌平区", "大兴区"]


def track_to_json(points: List[Dict[str, Any]]) -> str:
    """把轨迹点转成前端 Canvas 绘制用的紧凑 JSON。

    只保留绘图需要的字段，且经纬度转成 float（pymysql 取 DECIMAL 得到的是
    decimal.Decimal，不能直接 JSON 序列化）。
    """
    import json

    payload = [
        {
            "lng": float(p["longitude"]),
            "lat": float(p["latitude"]),
            "speed": float(p["speed"]) if p.get("speed") is not None else 0.0,
            "t": p["located_at"].strftime("%H:%M:%S") if p.get("located_at") else "",
        }
        for p in points
    ]
    # ensure_ascii=False 让中文可读；模板里用 safe 过滤避免被转义
    return json.dumps(payload, ensure_ascii=False)


def _track_json(points: List[Dict[str, Any]]) -> str:
    return track_to_json(points)


def _side_data(conn, user) -> Dict[str, Any]:
    """乘客端各页面共用的数据。"""
    return {
        "car_types": vehicle_repo.list_car_types(conn),
        "pricing": pricing_repo.list_current(conn),
        "addresses": address_repo.list_by_passenger(conn, user["profile_id"]),
        "regions": REGIONS,
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = require_role(request, "passenger")
    with get_db() as conn:
        recent = order_repo.list_by_passenger(conn, user["profile_id"], limit=5)
        active = [o for o in recent
                  if o["status"] in ("pending", "accepted", "arrived", "in_progress")]
        stats = {
            "total": len(order_repo.list_by_passenger(conn, user["profile_id"], limit=1000)),
            "completed": len(order_repo.list_by_passenger(
                conn, user["profile_id"], status="completed", limit=1000)),
        }
        side = _side_data(conn, user)

    return templates.TemplateResponse(
        "passenger/home.html",
        base_context(request, user=user, recent_orders=recent,
                     active_orders=active, stats=stats, **side),
    )


@router.get("/orders/new", response_class=HTMLResponse)
def order_new_page(request: Request):
    user = require_role(request, "passenger")
    with get_db() as conn:
        side = _side_data(conn, user)

    return templates.TemplateResponse(
        "passenger/order_new.html",
        base_context(request, user=user, **side),
    )


@router.post("/orders/new")
def order_new_submit(
    request: Request,
    car_type_id: int = Form(1),
    origin_address: str = Form(""),
    origin_region: str = Form(""),
    dest_address: str = Form(""),
    remark: str = Form(""),
    address_id: str = Form(""),
):
    user = require_role(request, "passenger")

    # 若选择了常用地址，用它的名称与坐标覆盖手填内容
    origin_lng = origin_lat = None
    origin_name = origin_address.strip()

    with get_db() as conn:
        if address_id.isdigit():
            addr = address_repo.get_by_id(conn, int(address_id))
            if addr and addr["passenger_id"] == user["profile_id"]:
                origin_name = addr["address_name"]
                origin_lng = float(addr["longitude"]) if addr["longitude"] is not None else None
                origin_lat = float(addr["latitude"]) if addr["latitude"] is not None else None
                if addr.get("latitude") is not None:
                    origin_region = origin_region or "朝阳区"

    ok, order_id, message = order_service.create_order(
        passenger_id=user["profile_id"],
        car_type_id=car_type_id,
        origin_address=origin_name,
        origin_lng=origin_lng,
        origin_lat=origin_lat,
        origin_region=origin_region or REGIONS[0],
        dest_address=dest_address.strip(),
        dest_lng=None,
        dest_lat=None,
        remark=remark.strip() or None,
        auto_dispatch=True,
        dispatch_size=3,
    )

    if not ok:
        with get_db() as conn:
            side = _side_data(conn, user)
        return templates.TemplateResponse(
            "passenger/order_new.html",
            base_context(request, user=user, error=message,
                         form={"car_type_id": car_type_id,
                               "origin_address": origin_address,
                               "origin_region": origin_region,
                               "dest_address": dest_address,
                               "remark": remark}, **side),
            status_code=400,
        )

    raise redirect_with_message(
        "/passenger/orders/%d" % order_id,
        "下单成功，正在为您寻找附近司机", "success")


@router.get("/orders", response_class=HTMLResponse)
def order_list(request: Request, status: str = ""):
    user = require_role(request, "passenger")
    status = status.strip() or None

    with get_db() as conn:
        orders = order_repo.list_by_passenger(
            conn, user["profile_id"], status=status, limit=100)
        counts = order_repo.list_by_passenger(conn, user["profile_id"], limit=1000)

    by_status: Dict[str, int] = {}
    for o in counts:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1

    return templates.TemplateResponse(
        "passenger/order_list.html",
        base_context(request, user=user, orders=orders,
                     current_status=status or "", by_status=by_status,
                     total=len(counts)),
    )


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(request: Request, order_id: int):
    user = require_role(request, "passenger")

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)
        if not order or order["passenger_id"] != user["profile_id"]:
            raise redirect_with_message("/passenger/orders", "订单不存在", "error")

        logs = order_repo.get_status_log(conn, order_id)
        duration = order_repo.get_duration(conn, order_id)
        dispatches = dispatch_repo.list_order_dispatches(conn, order_id)
        track_summary = order_repo.get_track_summary(conn, order_id)
        can_review, _ = order_service.can_review(order_id, user["profile_id"])

        from repositories import review_repo
        review = review_repo.get_by_order(conn, order_id)

    return templates.TemplateResponse(
        "passenger/order_detail.html",
        base_context(request, user=user, order=order, logs=logs,
                     duration=duration, dispatches=dispatches,
                     track_summary=track_summary,
                     can_review=can_review, review=review),
    )


@router.post("/orders/{order_id}/cancel")
def order_cancel(request: Request, order_id: int, reason: str = Form("")):
    user = require_role(request, "passenger")

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)
        if not order or order["passenger_id"] != user["profile_id"]:
            raise redirect_with_message("/passenger/orders", "订单不存在", "error")

    ok, message = order_service.cancel_order(
        order_id, "passenger", user["profile_id"], reason.strip() or "乘客取消")

    raise redirect_with_message(
        "/passenger/orders/%d" % order_id, message, "success" if ok else "error")


@router.get("/orders/{order_id}/review", response_class=HTMLResponse)
def review_page(request: Request, order_id: int):
    user = require_role(request, "passenger")

    ok, message = order_service.can_review(order_id, user["profile_id"])
    if not ok:
        raise redirect_with_message("/passenger/orders/%d" % order_id, message, "warning")

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)

    return templates.TemplateResponse(
        "passenger/review_form.html",
        base_context(request, user=user, order=order),
    )


@router.post("/orders/{order_id}/review")
def review_submit(
    request: Request,
    order_id: int,
    score: int = Form(5),
    content: str = Form(""),
    tags: List[str] = Form([]),
):
    user = require_role(request, "passenger")

    tag_str = ",".join(t.strip() for t in tags if t.strip()) or None
    ok, message = order_service.submit_review(
        order_id, user["profile_id"], score, content.strip() or None, tag_str)

    raise redirect_with_message(
        "/passenger/orders/%d" % order_id, message, "success" if ok else "error")


@router.get("/orders/{order_id}/track", response_class=HTMLResponse)
def order_track(request: Request, order_id: int):
    """选做：行程轨迹回放。用 Canvas 自绘折线，不依赖任何地图 API。"""
    user = require_role(request, "passenger")

    with get_db() as conn:
        order = order_repo.get_detail(conn, order_id)
        if not order or order["passenger_id"] != user["profile_id"]:
            raise redirect_with_message("/passenger/orders", "订单不存在", "error")
        points = order_repo.get_track(conn, order_id)
        summary = order_repo.get_track_summary(conn, order_id)

    return templates.TemplateResponse(
        "passenger/track.html",
        base_context(request, user=user, order=order,
                     points=points, summary=summary,
                     track_json=_track_json(points)),
    )


# -------------------------------------------------------------
# 常用地址管理
# -------------------------------------------------------------

@router.get("/addresses", response_class=HTMLResponse)
def address_list(request: Request):
    user = require_role(request, "passenger")
    with get_db() as conn:
        addresses = address_repo.list_by_passenger(conn, user["profile_id"])

    return templates.TemplateResponse(
        "passenger/address_list.html",
        base_context(request, user=user, addresses=addresses, regions=REGIONS),
    )


@router.post("/addresses/new")
def address_create(
    request: Request,
    label: str = Form(""),
    address_name: str = Form(""),
    longitude: str = Form(""),
    latitude: str = Form(""),
    is_default: str = Form(""),
):
    user = require_role(request, "passenger")

    if not label.strip() or not address_name.strip():
        raise redirect_with_message("/passenger/addresses", "标签和地址都不能为空", "error")

    lng = float(longitude) if longitude.strip() else None
    lat = float(latitude) if latitude.strip() else None

    with get_db() as conn:
        try:
            address_repo.create(conn, user["profile_id"], label.strip(),
                                address_name.strip(), lng, lat,
                                1 if is_default else 0)
        except Exception:
            # 唯一键 (passenger_id, label) 冲突
            raise redirect_with_message(
                "/passenger/addresses", "该标签已存在，请换一个标签", "error")

    raise redirect_with_message("/passenger/addresses", "地址已添加", "success")


@router.post("/addresses/{address_id}/update")
def address_update(
    request: Request,
    address_id: int,
    label: str = Form(""),
    address_name: str = Form(""),
    longitude: str = Form(""),
    latitude: str = Form(""),
    is_default: str = Form(""),
):
    user = require_role(request, "passenger")

    lng = float(longitude) if longitude.strip() else None
    lat = float(latitude) if latitude.strip() else None

    with get_db() as conn:
        affected = address_repo.update(
            conn, address_id, user["profile_id"], label.strip(),
            address_name.strip(), lng, lat, 1 if is_default else 0)

    raise redirect_with_message(
        "/passenger/addresses",
        "地址已更新" if affected else "地址不存在",
        "success" if affected else "error")


@router.post("/addresses/{address_id}/delete")
def address_delete(request: Request, address_id: int):
    user = require_role(request, "passenger")
    with get_db() as conn:
        affected = address_repo.delete(conn, address_id, user["profile_id"])

    raise redirect_with_message(
        "/passenger/addresses",
        "地址已删除" if affected else "地址不存在",
        "success" if affected else "error")


@router.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = require_role(request, "passenger")
    return templates.TemplateResponse(
        "passenger/profile.html", base_context(request, user=user))
