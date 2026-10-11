# -*- coding: utf-8 -*-
"""账号表数据访问。

所有 SQL 均为参数化查询（%s 占位符 + 参数元组），绝不字符串拼接 —— 防 SQL 注入。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.connection import transaction


def find_by_username(conn, username: str) -> Optional[Dict[str, Any]]:
    """按登录名取账号（含哈希与盐，供认证使用）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `account_id`,`username`,`password_hash`,`salt`,`pwd_iterations`,"
            "       `pwd_algo`,`role_code`,`status`"
            "  FROM `account` WHERE `username` = %s",
            (username,),
        )
        return cur.fetchone()


def find_by_id(conn, account_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `account_id`,`username`,`role_code`,`status`,`created_at`"
            "  FROM `account` WHERE `account_id` = %s",
            (account_id,),
        )
        return cur.fetchone()


def username_exists(conn, username: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM `account` WHERE `username` = %s LIMIT 1", (username,))
        return cur.fetchone() is not None


def create(conn, username: str, password_hash: str, salt: str,
           iterations: int, role_code: str, password_algo: str = "pbkdf2_sha256") -> int:
    """新建账号，返回 account_id。调用方需自行管理事务边界。"""
    with transaction(conn) as cur:
        cur.execute(
            "INSERT INTO `account`"
            " (`username`,`password_hash`,`salt`,`pwd_iterations`,`pwd_algo`,`role_code`,`status`)"
            " VALUES (%s,%s,%s,%s,%s,%s,1)",
            (username, password_hash, salt, iterations, password_algo, role_code),
        )
        return cur.lastrowid


def update_password(conn, account_id: int, password_hash: str,
                    salt: str, iterations: int) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `account`"
            "   SET `password_hash`=%s, `salt`=%s, `pwd_iterations`=%s"
            " WHERE `account_id`=%s",
            (password_hash, salt, iterations, account_id),
        )


def set_status(conn, account_id: int, status: int) -> int:
    with transaction(conn) as cur:
        return cur.execute(
            "UPDATE `account` SET `status` = %s WHERE `account_id` = %s",
            (status, account_id),
        )


def list_by_role(conn, role_code: str) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT `account_id`,`username`,`role_code`,`status`,`created_at`"
            "  FROM `account` WHERE `role_code` = %s ORDER BY `account_id`",
            (role_code,),
        )
        return list(cur.fetchall())
