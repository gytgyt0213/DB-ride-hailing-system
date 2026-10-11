# -*- coding: utf-8 -*-
"""网约车（打车）系统 —— FastAPI 应用入口。

启动：
    F:\\pythonnewversion\\py39ver\\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

访问： http://127.0.0.1:8000
"""
from __future__ import annotations

import sys

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import STATIC_DIR, AppConfig, DBConfig
from db.connection import ping
from routers import admin_router, auth_router, driver_router, passenger_router
from routers.deps import RedirectException, base_context, templates
from services.auth_service import home_url_for_role

# Windows 控制台默认不是 UTF-8，避免中文日志乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


app = FastAPI(
    title=AppConfig.app_name,
    description="数据库课程大作业：网约车平台（MySQL + FastAPI + 手写 SQL）",
    version="1.0.0",
)

# 静态资源
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 路由
app.include_router(auth_router.router)
app.include_router(passenger_router.router)
app.include_router(driver_router.router)
app.include_router(admin_router.router)


# =============================================================
# 异常处理
# =============================================================

@app.exception_handler(RedirectException)
async def redirect_exception_handler(request: Request, exc: RedirectException):
    """把业务跳转（未登录 / 无权访问 / 操作后回跳）转成 303。"""
    url = exc.url
    if exc.message:
        sep = "&" if "?" in url else "?"
        from urllib.parse import quote
        url = "%s%smsg=%s&level=%s" % (
            url, sep, quote(exc.message, safe=""), exc.level)
    return RedirectResponse(url, status_code=303)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        "error.html",
        base_context(request, code=404, message="页面不存在",
                     detail="请检查地址是否正确，或返回首页重新进入。"),
        status_code=404,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(
        "error.html",
        base_context(request, code=500, message="服务器内部错误",
                     detail="请查看运行 uvicorn 的终端窗口中的错误堆栈。"),
        status_code=500,
    )


# =============================================================
# 基础页面
# =============================================================

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """首页：按登录状态分流。"""
    from routers.deps import current_user

    user = current_user(request)
    if user:
        return RedirectResponse(home_url_for_role(user["role_code"]), status_code=303)
    return RedirectResponse("/auth/login", status_code=303)


@app.get("/health")
def health():
    """健康检查：确认服务与数据库连接都正常。"""
    db_ok = ping()
    return JSONResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "app": AppConfig.app_name,
            "database": {
                "connected": db_ok,
                "target": DBConfig.describe(),
            },
        },
        status_code=200 if db_ok else 503,
    )


@app.on_event("startup")
def on_startup() -> None:
    """启动时检查数据库连通性，并把关键提示打到控制台。"""
    line = "=" * 68
    print(line)
    print("  %s" % AppConfig.app_name)
    print(line)
    print("  数据库：%s" % DBConfig.describe())

    if ping():
        print("  数据库连接：正常")
        print("  演示账号：admin/admin123   d001~d030/123456   p001~p050/123456")
        print("  访问地址：http://127.0.0.1:8000")
    else:
        print("  ⚠️  数据库连接失败！")
        print("     请检查：")
        print("       1. MySQL 服务是否已启动")
        print("       2. .env 中的 DB_USER / DB_PASSWORD 是否正确")
        print("       3. 是否已执行 sql/00 ~ sql/06 完成建库与数据初始化")
    print(line)
