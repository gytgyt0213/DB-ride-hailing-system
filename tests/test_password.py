# -*- coding: utf-8 -*-
"""密码哈希的单元测试（不依赖数据库）。

运行：
    F:\\pythonnewversion\\py39ver\\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from security.password import (DEFAULT_ITERATIONS, hash_password,  # noqa: E402
                               verify_password)


class TestPasswordHashing(unittest.TestCase):
    """验证 PBKDF2 加盐哈希的正确性与安全性属性。"""

    def test_roundtrip(self):
        """正确密码应校验通过。"""
        salt, digest, iterations = hash_password("mySecret123")
        self.assertTrue(verify_password("mySecret123", salt, digest, iterations))

    def test_wrong_password_rejected(self):
        """错误密码应校验失败。"""
        salt, digest, iterations = hash_password("mySecret123")
        self.assertFalse(verify_password("mySecret124", salt, digest, iterations))
        self.assertFalse(verify_password("", salt, digest, iterations))
        self.assertFalse(verify_password("MySecret123", salt, digest, iterations))

    def test_salt_is_random_per_call(self):
        """同一个密码两次哈希必须得到不同的盐与不同的哈希值。

        这是防彩虹表的关键：如果盐固定，相同密码就会产生相同哈希，
        攻击者可以批量比对预计算表。若本测试失败，说明盐没有随机化。
        """
        salt1, digest1, _ = hash_password("samePassword")
        salt2, digest2, _ = hash_password("samePassword")

        self.assertNotEqual(salt1, salt2, "两次生成的盐不应相同")
        self.assertNotEqual(digest1, digest2, "相同密码 + 不同盐应产生不同哈希")

    def test_field_lengths_match_schema(self):
        """输出长度必须与数据库列定义一致，否则严格模式下会插入失败。

        account.salt           CHAR(32)  <- 16 字节 hex
        account.password_hash  CHAR(64)  <- 32 字节 hex
        """
        salt, digest, iterations = hash_password("x")
        self.assertEqual(len(salt), 32, "salt 应为 16 字节的 hex（32 字符）")
        self.assertEqual(len(digest), 64, "hash 应为 32 字节的 hex（64 字符）")
        self.assertEqual(iterations, DEFAULT_ITERATIONS)

    def test_custom_iterations_stored_per_row(self):
        """迭代次数应可自定义并随行存储。

        这样将来提升哈希强度时，老账号仍能用其历史次数校验，不会集体失效。
        """
        salt, digest, iterations = hash_password("pw", iterations=1000)
        self.assertEqual(iterations, 1000)
        self.assertTrue(verify_password("pw", salt, digest, 1000))
        # 用错误的迭代次数校验必然失败
        self.assertFalse(verify_password("pw", salt, digest, 2000))

    def test_malformed_input_does_not_raise(self):
        """畸形输入应返回 False 而不是抛异常（避免认证接口 500）。"""
        salt, digest, _ = hash_password("pw")
        self.assertFalse(verify_password("pw", "not-hex", digest))
        self.assertFalse(verify_password("pw", salt, ""))
        self.assertFalse(verify_password("pw", salt, "abc"))

    def test_case_insensitive_hash_comparison(self):
        """哈希值以大写形式存储时也应能校验通过（本项目统一存小写 hex）。"""
        salt, digest, iterations = hash_password("pw")
        self.assertTrue(verify_password("pw", salt, digest.upper(), iterations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
