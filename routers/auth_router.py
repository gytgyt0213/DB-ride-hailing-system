# -*- coding: utf-8 -*-
"""认证路由：登录、注册、登出。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.connection import get_db
from routers.deps import RedirectException, base_context, redirect_with_message, templates
from security import session
from services import auth_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: Optional[str] = None):
    # 已登录的直接送回各自首页，避免重复登录
    user = base_context(request)["user"]
    if user:
        return RedirectResponse(auth_service.home_url_for_role(user["role_code"]),
                                status_code=303)

    return templates.TemplateResponse(
        "login.html",
        base_context(request, next_url=next or ""),
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
):
    account, message = auth_service.login(username, password)

    if account is None:
        return templates.TemplateResponse(
            "login.html",
            base_context(request, error=message, username=username, next_url=next),
            status_code=400,
        )

    with get_db() as conn:
        token = session.create_session(
            conn,
            account["account_id"],
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )

    target = next if next and next.startswith("/") else auth_service.home_url_for_role(
        account["role_code"])

    response = RedirectResponse(target, status_code=303)
    session.set_session_cookie(response, token)
    return response


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    user = base_context(request)["user"]
    if user:
        return RedirectResponse(auth_service.home_url_for_role(user["role_code"]),
                                status_code=303)
    return templates.TemplateResponse("register.html", base_context(request))


@router.post("/register")
def register_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
    real_name: str = Form(""),
    phone: str = Form(""),
    gender: int = Form(0),
    home_region: str = Form(""),
):
    ok, message = auth_service.register_passenger(
        username, password, confirm, real_name, phone, gender,
        home_region.strip() or None,
    )

    if not ok:
        return templates.TemplateResponse(
            "register.html",
            base_context(
                request, error=message,
                username=username, real_name=real_name, phone=phone,
                gender=gender, home_region=home_region,
            ),
            status_code=400,
        )

    raise redirect_with_message("/auth/login", message, "success")


@router.get("/logout")
def logout(request: Request):
    token = request.cookies.get(session.SessionConfig.cookie_name)
    with get_db() as conn:
        session.destroy_session(conn, token)

    response = RedirectResponse("/auth/login", status_code=303)
    session.clear_session_cookie(response)
    return response


@router.get("/password", response_class=HTMLResponse)
def password_page(request: Request):
    user = base_context(request)["user"]
    if not user:
        raise RedirectException("/auth/login", "请先登录")
    return templates.TemplateResponse("password.html", base_context(request, user=user))


@router.post("/password")
def password_submit(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(""),
    confirm: str = Form(""),
):
    user = base_context(request)["user"]
    if not user:
        raise RedirectException("/auth/login", "请先登录")

    ok, message = auth_service.change_password(
        user["account_id"], old_password, new_password, confirm)

    if not ok:
        return templates.TemplateResponse(
            "password.html",
            base_context(request, user=user, error=message),
            status_code=400,
        )

    # 密码已改，销毁当前会话强制重新登录
    token = request.cookies.get(session.SessionConfig.cookie_name)
    with get_db() as conn:
        session.destroy_session(conn, token)

    response = RedirectResponse(
        "/auth/login?msg=%s&level=success" % "密码已修改，请重新登录",
        status_code=303,
    )
    session.clear_session_cookie(response)
    return response
