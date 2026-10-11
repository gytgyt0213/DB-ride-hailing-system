# -*- coding: utf-8 -*-
"""数据库连接与事务管理。

设计要点（本项目手写 SQL、不用 ORM）：

1. pymysql 的 Connection **不是线程安全的**，而 FastAPI 的同步路由跑在线程池里。
   因此绝不复用全局连接——每次请求独立开一个连接，用完关闭。
   （用脚本时用 `with get_db() as conn` 即可。）

2. autocommit 默认关闭。所有多表写操作一律走 `transaction()` 上下文管理器，
   杜绝「忘记 commit 丢数据」和「忘记回滚导致长时间持锁」这两个经典坑。

3. 所有 SQL 一律使用 %s 占位符 + 参数元组，绝不字符串拼接（防注入）。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

import pymysql
from pymysql.cursors import DictCursor

from config import DBConfig


def create_connection() -> pymysql.connections.Connection:
    """新建一条数据库连接（调用方负责关闭）。"""
    return pymysql.connect(
        host=DBConfig.host,
        port=DBConfig.port,
        user=DBConfig.user,
        password=DBConfig.password,
        database=DBConfig.database,
        charset=DBConfig.charset,
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def get_db() -> Iterator[pymysql.connections.Connection]:
    """连接上下文：保证异常时回滚、结束时关闭。"""
    conn = create_connection()
    try:
        yield conn
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


@contextmanager
def transaction(conn: pymysql.connections.Connection) -> Iterator[DictCursor]:
    """事务上下文：正常提交，异常回滚后重抛。

    用法：
        with get_db() as conn:
            with transaction(conn) as cur:
                cur.execute("UPDATE ...")
                cur.execute("INSERT ...")
    """
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# -------------------------------------------------------------
# 查询辅助函数：只读查询的便捷封装
# -------------------------------------------------------------

def query_all(sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """执行查询，返回全部行（list of dict）。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())


def query_one(sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """执行查询，返回首行；无结果返回 None。"""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()


def query_scalar(sql: str, params: Optional[Sequence[Any]] = None) -> Any:
    """执行查询，返回首行第一列的值。"""
    row = query_one(sql, params)
    if row is None:
        return None
    return next(iter(row.values()))


def execute(sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """执行单条写语句，返回影响行数。"""
    with get_db() as conn:
        with transaction(conn) as cur:
            affected = cur.execute(sql, params or ())
            return affected


def ping() -> bool:
    """连通性检查，供 /health 使用。"""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False
