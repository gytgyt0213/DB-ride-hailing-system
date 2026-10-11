# -*- coding: utf-8 -*-
"""路由公共依赖：模板环境、登录态、角色校验、提示消息。

【为什么用 RedirectException 而不是 HTTPException】
  这是服务端渲染的多页应用，权限不足时应该 303 跳转到登录页，
  而不是返回一个 JSON 错误体。FastAPI 的 HTTPException 默认返回 JSON，
  因此自定义一个异常，在 main.py 里注册处理器统一转成 RedirectResponse。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import Request
from fastapi.templating import Jinja2Templates

from config import AppConfig, TEMPLATES_DIR
from db.connection import get_db
from security import session

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class RedirectException(Exception):
    """抛出后由 main.py 的异常处理器转成 303 跳转。"""

    def __init__(self, url: str, message: Optional[str] = None,
                 level: str = "info") -> None:
        self.url = url
        self.message = message
        self.level = level
        super().__init__(url)


def current_user(request: Request) -> Optional[Dict[str, Any]]:
    """从 Cookie 取会话并载入用户信息；未登录返回 None。"""
    token = request.cookies.get(session.SessionConfig.cookie_name)
    if not token:
        return None
    with get_db() as conn:
        return session.load_session(conn, token)


def require_login(request: Request, next_url: Optional[str] = None) -> Dict[str, Any]:
    """要求已登录，否则跳转登录页。返回当前用户。"""
    user = current_user(request)
    if user is None:
        target = "/auth/login"
        nxt = next_url or str(request.url.path)
        if nxt and not nxt.startswith("/auth/"):
            target += "?next=" + quote(nxt, safe="")
        raise RedirectException(target, "请先登录")
    return user


def require_role(request: Request, role_code: str,
                 next_url: Optional[str] = None) -> Dict[str, Any]:
    """要求已登录且角色匹配，否则跳转。返回当前用户。"""
    user = require_login(request, next_url)
    if user["role_code"] != role_code:
        # 已登录但角色不对：送他回自己的首页，而不是粗暴报 403
        from services.auth_service import home_url_for_role
        raise RedirectException(
            home_url_for_role(user["role_code"]),
            "当前账号无权访问该页面",
            "warning",
        )
    return user


def flash_from_query(request: Request) -> Dict[str, str]:
    """从查询参数读取一次性提示（msg / level），供模板渲染。

    用查询参数而不是服务端 session 存 flash，是为了让提示可刷新、
    可分享链接，且不需要额外的会话状态。
    """
    return {
        "flash_message": request.query_params.get("msg", ""),
        "flash_level": request.query_params.get("level", "info"),
    }


def base_context(request: Request, user: Optional[Dict[str, Any]] = None,
                 **extra: Any) -> Dict[str, Any]:
    """所有页面共用的模板上下文。"""
    ctx: Dict[str, Any] = {
        "request": request,
        "user": user if user is not None else current_user(request),
        "app_name": AppConfig.app_name,
        # 模板里经常需要拿当前时间做比较（如邀请是否过期），统一注入
        "now": datetime.now(),
    }
    ctx.update(flash_from_query(request))
    ctx.update(extra)
    return ctx


def redirect_with_message(url: str, message: str, level: str = "info") -> RedirectException:
    """构造一个带提示信息的跳转（提示通过查询参数传递）。"""
    sep = "&" if "?" in url else "?"
    return RedirectException(
        "%s%smsg=%s&level=%s" % (url, sep, quote(message, safe=""), level)
    )
