# -*- coding: utf-8 -*-
"""认证业务：登录、乘客自助注册、修改密码。"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from db.connection import get_db, transaction
from repositories import account_repo, driver_repo, passenger_repo
from security.password import DEFAULT_ITERATIONS, hash_password, verify_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

MIN_PASSWORD_LEN = 6


def login(username: str, password: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """校验账号密码。

    返回 (account, message)：
      account 为 None 表示失败，message 为失败原因；
      成功时 account 为账号行（含 role_code），message 为空串。

    安全说明：无论「用户不存在」还是「密码错误」，返回给前端的提示保持一致，
    避免通过提示差异枚举出系统里存在哪些用户名。
    """
    username = (username or "").strip()
    if not username or not password:
        return None, "请输入用户名和密码"

    with get_db() as conn:
        account = account_repo.find_by_username(conn, username)

        if account is None:
            return None, "用户名或密码错误"

        if account["status"] != 1:
            return None, "该账号已被禁用，请联系管理员"

        ok = verify_password(
            password,
            account["salt"],
            account["password_hash"],
            account.get("pwd_iterations") or DEFAULT_ITERATIONS,
        )
        if not ok:
            return None, "用户名或密码错误"

        # 角色档案必须存在，否则登录后无法定位身份
        profile_id = None
        if account["role_code"] == "passenger":
            p = passenger_repo.get_by_account(conn, account["account_id"])
            profile_id = p["passenger_id"] if p else None
        elif account["role_code"] == "driver":
            d = driver_repo.get_by_account(conn, account["account_id"])
            profile_id = d["driver_id"] if d else None
        else:
            profile_id = account["account_id"]

        if profile_id is None:
            return None, "账号档案不完整，请联系管理员"

        account["profile_id"] = profile_id
        return account, ""


def register_passenger(
    username: str,
    password: str,
    confirm: str,
    real_name: str,
    phone: str,
    gender: int = 0,
    home_region: Optional[str] = None,
) -> Tuple[bool, str]:
    """乘客自助注册。账号与档案在同一个连接内创建。"""
    username = (username or "").strip()
    real_name = (real_name or "").strip()
    phone = (phone or "").strip()

    if not USERNAME_RE.match(username):
        return False, "用户名需为 3~20 位字母、数字或下划线"
    if len(password or "") < MIN_PASSWORD_LEN:
        return False, "密码长度至少 %d 位" % MIN_PASSWORD_LEN
    if password != confirm:
        return False, "两次输入的密码不一致"
    if not real_name:
        return False, "请填写姓名"
    if not PHONE_RE.match(phone):
        return False, "请填写正确的手机号"
    if gender not in (0, 1, 2):
        gender = 0

    with get_db() as conn:
        if account_repo.username_exists(conn, username):
            return False, "该用户名已被注册"

        salt, pwd_hash, iterations = hash_password(password)

        # 账号与档案要么一起成功，要么一起失败
        with transaction(conn) as cur:
            cur.execute(
                "INSERT INTO `account`"
                " (`username`,`password_hash`,`salt`,`pwd_iterations`,`pwd_algo`,"
                "  `role_code`,`status`)"
                " VALUES (%s,%s,%s,%s,'pbkdf2_sha256','passenger',1)",
                (username, pwd_hash, salt, iterations),
            )
            account_id = cur.lastrowid

            cur.execute(
                "INSERT INTO `passenger`"
                " (`account_id`,`real_name`,`phone`,`gender`,`home_region`,`status`)"
                " VALUES (%s,%s,%s,%s,%s,1)",
                (account_id, real_name, phone, gender, home_region),
            )

    return True, "注册成功，请登录"


def create_account_for_role(username: str, password: str, role_code: str) -> Tuple[Optional[int], str]:
    """管理员创建司机账号（或其它非乘客账号）。返回 (account_id, message)。"""
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        return None, "用户名需为 3~20 位字母、数字或下划线"
    if len(password or "") < MIN_PASSWORD_LEN:
        return None, "密码长度至少 %d 位" % MIN_PASSWORD_LEN
    if role_code not in ("admin", "driver", "passenger"):
        return None, "角色不合法"

    with get_db() as conn:
        if account_repo.username_exists(conn, username):
            return None, "该用户名已存在"

        salt, pwd_hash, iterations = hash_password(password)
        account_id = account_repo.create(
            conn, username, pwd_hash, salt, iterations, role_code
        )
        return account_id, ""


def change_password(account_id: int, old_password: str, new_password: str,
                    confirm: str) -> Tuple[bool, str]:
    if len(new_password or "") < MIN_PASSWORD_LEN:
        return False, "新密码长度至少 %d 位" % MIN_PASSWORD_LEN
    if new_password != confirm:
        return False, "两次输入的新密码不一致"

    with get_db() as conn:
        account = account_repo.find_by_id(conn, account_id)
        if not account:
            return False, "账号不存在"

        full = account_repo.find_by_username(conn, account["username"])
        if not full or not verify_password(
            old_password, full["salt"], full["password_hash"],
            full.get("pwd_iterations") or DEFAULT_ITERATIONS,
        ):
            return False, "原密码不正确"

        salt, pwd_hash, iterations = hash_password(new_password)
        account_repo.update_password(conn, account_id, pwd_hash, salt, iterations)

    return True, "密码修改成功"


def home_url_for_role(role_code: str) -> str:
    return {
        "admin": "/admin/",
        "driver": "/driver/",
        "passenger": "/passenger/",
    }.get(role_code, "/")
