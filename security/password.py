# -*- coding: utf-8 -*-
"""密码哈希：PBKDF2-HMAC-SHA256。

为什么不用 passlib + bcrypt：
  该组合在 bcrypt 4.x 上有已知的版本探测兼容问题（读取 bcrypt.__about__ 失败），
  容易在演示/答辩现场才暴露。标准库的 hashlib.pbkdf2_hmac 零依赖、行为稳定，
  安全性对本课程作业完全够用。

方案细节（也是数据库设计的一部分）：
  * 每个用户独立的随机盐 os.urandom(16)，以 hex 单独存 `account.salt` 列
    —— 绝不用全局固定盐，否则相同密码产生相同哈希，可被彩虹表批量破解
  * 迭代次数随行存在 `account.pwd_iterations` 列
    —— 将来提升迭代次数时，老账号仍能用其历史次数校验，不会集体失效
  * 算法标识存 `account.pwd_algo` 列，为将来迁移算法留出空间
  * 比对用 hmac.compare_digest —— 恒定时间比较，防时序侧信道
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Tuple

ALGO = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 200000
SALT_BYTES = 16
HASH_BYTES = 32


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> Tuple[str, str, int]:
    """生成随机盐并计算哈希。

    返回 (salt_hex, hash_hex, iterations)，三个值分别存入 account 表的
    salt / password_hash / pwd_iterations 三列。
    """
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=HASH_BYTES,
    )
    return salt.hex(), digest.hex(), iterations


def verify_password(
    password: str,
    salt_hex: str,
    expected_hash: str,
    iterations: int = DEFAULT_ITERATIONS,
) -> bool:
    """校验密码。使用恒定时间比较，避免时序攻击。"""
    try:
        salt = bytes.fromhex(salt_hex)
    except (ValueError, TypeError):
        return False

    if not expected_hash:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=HASH_BYTES,
    )
    return hmac.compare_digest(digest.hex(), expected_hash.lower())
