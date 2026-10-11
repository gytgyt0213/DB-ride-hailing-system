# -*- coding: utf-8 -*-
"""服务端会话：会话数据存在数据库 user_session 表里。

为什么用「服务端会话表」而不是签名 Cookie（如 itsdangerous）：
  1. 这是数据库课程作业，把会话也纳入数据库设计，能多一张有实际意义的表，
     并且可以演示「会话过期清理」这类定时任务；
  2. 签名 Cookie 无法主动失效（用户改密码后旧 token 仍有效），
     服务端会话只需 DELETE 一行即可踢下线；
  3. 本机未安装 itsdangerous，用标准库 + 数据库表即可实现，零额外依赖。

安全要点：
  * 令牌用 secrets.token_hex(32) 生成（密码学安全随机数，非 random 模块）
  * Cookie 设 HttpOnly（防 XSS 读取）；SameSite=Lax（防 CSRF）
  * 每次校验都检查 expires_at，过期即视为无效
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from config import SessionConfig
from db.connection import transaction

TOKEN_BYTES = 32          # 32 字节 -> 64 字符 hex，正好放进 CHAR(64)


def new_token() -> str:
    """生成会话令牌。secrets 模块是密码学安全的，不要用 random。"""
    return secrets.token_hex(TOKEN_BYTES)


def create_session(
    conn,
    account_id: int,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> str:
    """创建会话并返回令牌。"""
    token = new_token()
    expires_at = datetime.now() + timedelta(seconds=SessionConfig.lifetime_seconds)
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO `user_session`"
            " (`session_id`,`account_id`,`expires_at`,`ip`,`user_agent`)"
            " VALUES (%s,%s,%s,%s,%s)",
            (token, account_id, expires_at, ip, (user_agent or "")[:200] or None),
        )
    return token


def load_session(conn, token: Optional[str]) -> Optional[Dict[str, Any]]:
    """按令牌取会话，并连带取出账号与角色档案信息。

    返回 None 表示：无令牌 / 令牌不存在 / 已过期 / 账号被禁用。
    """
    if not token:
        return None

    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.`session_id`, s.`account_id`, s.`expires_at`,"
            "       a.`username`, a.`role_code`, a.`status` AS `account_status`"
            "  FROM `user_session` s"
            "  JOIN `account` a ON a.`account_id` = s.`account_id`"
            " WHERE s.`session_id` = %s AND s.`expires_at` > NOW()",
            (token,),
        )
        row = cur.fetchone()

    if not row or row["account_status"] != 1:
        return None

    # 按角色补充档案 ID，便于后续查询直接使用
    role = row["role_code"]
    profile_id = None
    extra: Dict[str, Any] = {}

    with conn.cursor() as cur:
        if role == "passenger":
            cur.execute(
                "SELECT `passenger_id`,`real_name`,`phone`,`home_region`"
                "  FROM `passenger` WHERE `account_id` = %s",
                (row["account_id"],),
            )
            p = cur.fetchone()
            if p:
                profile_id = p["passenger_id"]
                extra = {"real_name": p["real_name"], "phone": p["phone"],
                         "home_region": p["home_region"]}
        elif role == "driver":
            cur.execute(
                "SELECT `driver_id`,`real_name`,`phone`,`region`,"
                "       `online_status`,`audit_status`,`service_score`,`review_count`"
                "  FROM `driver` WHERE `account_id` = %s",
                (row["account_id"],),
            )
            d = cur.fetchone()
            if d:
                profile_id = d["driver_id"]
                extra = {"real_name": d["real_name"], "phone": d["phone"],
                         "region": d["region"], "online_status": d["online_status"],
                         "audit_status": d["audit_status"],
                         "service_score": d["service_score"],
                         "review_count": d["review_count"]}
        elif role == "admin":
            cur.execute(
                "SELECT `admin_id`,`real_name`,`phone` FROM `admin` WHERE `account_id` = %s",
                (row["account_id"],),
            )
            a = cur.fetchone()
            if a:
                profile_id = a["admin_id"]
                extra = {"real_name": a["real_name"], "phone": a["phone"]}

    if profile_id is None:
        # 账号存在但档案缺失（数据不一致），视为未登录
        return None

    result: Dict[str, Any] = {
        "session_id": row["session_id"],
        "account_id": row["account_id"],
        "username": row["username"],
        "role_code": role,
        "profile_id": profile_id,
    }
    result.update(extra)
    return result


def destroy_session(conn, token: Optional[str]) -> None:
    """登出：删除会话行。"""
    if not token:
        return
    with transaction(conn) as cur:
        cur.execute("DELETE FROM `user_session` WHERE `session_id` = %s", (token,))


def cleanup_expired(conn) -> int:
    """清理过期会话，返回删除条数。可由定时任务调用。"""
    with transaction(conn) as cur:
        cur.execute("DELETE FROM `user_session` WHERE `expires_at` <= NOW()")
        return cur.rowcount


def set_session_cookie(response, token: str) -> None:
    """把会话令牌写入 Cookie。"""
    response.set_cookie(
        key=SessionConfig.cookie_name,
        value=token,
        max_age=SessionConfig.lifetime_seconds,
        httponly=True,          # JS 读不到，防 XSS 窃取
        samesite="lax",         # 防 CSRF
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key=SessionConfig.cookie_name, path="/")
