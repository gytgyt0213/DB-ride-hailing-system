# -*- coding: utf-8 -*-
"""存储过程调用封装。

pymysql 没有原生的 OUT 参数支持，标准做法是：
    CALL proc(%s, %s, @out1, @out2)     然后     SELECT @out1, @out2
用户变量是**连接级**的，所以必须在同一个连接里取回。

⚠️ 调用存储过程后连接上可能残留未读完的结果集，
   必须用 nextset() 清空，否则同一连接上的后续语句会报
   "Commands out of sync"。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def call_with_out(
    conn,
    proc_name: str,
    in_params: Sequence[Any],
    out_params: List[str],
) -> Dict[str, Any]:
    """调用带 OUT 参数的存储过程，返回 {out参数名: 值} 字典。

    proc_name 需带反引号，如 "`sp_create_order`"。
    """
    placeholders = ", ".join(["%s"] * len(in_params))
    out_vars = ", ".join("@" + name for name in out_params)
    call_sql = "CALL %s(%s%s)" % (
        proc_name,
        placeholders,
        (", " + out_vars) if out_vars else "",
    )

    cur = conn.cursor()
    try:
        cur.execute(call_sql, tuple(in_params))
        # 清空过程可能产生的额外结果集
        while cur.nextset():
            pass

        if out_params:
            select_sql = "SELECT " + ", ".join(
                "@%s AS `%s`" % (name, name) for name in out_params
            )
            cur.execute(select_sql)
            return cur.fetchone() or {}
        return {}
    finally:
        cur.close()
