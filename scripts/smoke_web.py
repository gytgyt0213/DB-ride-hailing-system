# -*- coding: utf-8 -*-
"""Web 冒烟测试：用真实 HTTP 请求把三端主要页面走一遍。

运行（需先启动 uvicorn）：
    F:\\pythonnewversion\\py39ver\\python.exe scripts\\smoke_web.py

用标准库 urllib + http.cookiejar，不依赖 requests。
每个角色登录后带 Cookie 访问各页面，检查 HTTP 状态码与关键内容。
"""
from __future__ import annotations

import http.cookiejar
import sys
import urllib.error
import urllib.parse
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "http://127.0.0.1:8000"


class Session(object):
    """带 Cookie 的简易会话。"""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path):
        try:
            with self.opener.open(BASE + path, timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return 0, repr(e)

    def post(self, path, data):
        body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
        req = urllib.request.Request(BASE + path, data=body, method="POST")
        try:
            with self.opener.open(req, timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            return 0, repr(e)


PASS = []
FAIL = []


def check(name, status, body, expect=200, must_contain=None):
    ok = status == expect
    if ok and must_contain:
        ok = all(s in body for s in must_contain)
    if ok:
        PASS.append(name)
        print("  [OK]   %-46s %s" % (name, status))
    else:
        FAIL.append((name, status, must_contain))
        print("  [FAIL] %-46s %s" % (name, status))
        if must_contain:
            missing = [s for s in must_contain if s not in body]
            if missing:
                print("         缺少内容: %s" % missing)
        # 打印一小段页面内容帮助定位
        snippet = body[:400].replace("\n", " ")
        print("         片段: %s" % snippet)


def login(username, password):
    s = Session()
    status, body = s.post("/auth/login", {"username": username, "password": password,
                                          "next": ""})
    return s, status


def main():
    print("=" * 72)
    print("Web 冒烟测试  ->  %s" % BASE)
    print("=" * 72)

    # ---------- 未登录 ----------
    print("\n[未登录]")
    s = Session()
    st, body = s.get("/health")
    check("健康检查 /health", st, body, 200, ['"connected":true', '"status":"ok"'])

    st, body = s.get("/auth/login")
    check("登录页", st, body, 200, ["演示账号"])

    st, body = s.get("/auth/register")
    check("注册页", st, body, 200, ["乘客注册"])

    st, body = s.get("/passenger/")
    check("未登录访问乘客端应跳转", st, body, 200, ["登录"])   # 303 被 urllib 跟随

    # ---------- 管理员 ----------
    print("\n[管理员 admin]")
    s, st = login("admin", "admin123")
    check("管理员登录", st, "", 200)

    for path, name, contains in [
        ("/admin/", "管理总览", ["管理总览", "订单状态分布"]),
        ("/admin/drivers", "司机管理", ["司机管理", "新建司机账号"]),
        ("/admin/vehicles", "车辆管理", ["车辆管理"]),
        ("/admin/pricing", "计价规则", ["计价规则管理", "发布新版本"]),
        ("/admin/passengers", "乘客管理", ["乘客管理"]),
        ("/admin/orders", "订单监控", ["订单监控"]),
        ("/admin/stats", "统计报表", ["统计报表", "司机接单排行"]),
        ("/admin/regions", "区域供需统计", ["区域供需统计", "供需比"]),
        ("/admin/licenses", "证照到期提醒", ["证照与保险到期提醒"]),
        ("/admin/reviews", "评价管理", ["评价与服务分", "一致性"]),
        ("/admin/maintenance", "数据库维护", ["数据库维护"]),
    ]:
        st, body = s.get(path)
        check(name + " " + path, st, body, 200, contains)

    # 取一个订单详情
    st, body = s.get("/admin/orders?status=completed")
    check("订单监控（筛已完成）", st, body, 200, ["订单监控"])

    # ---------- 乘客 ----------
    print("\n[乘客 p001]")
    s, st = login("p001", "123456")
    check("乘客登录", st, "", 200)

    for path, name, contains in [
        ("/passenger/", "乘客首页", ["您好", "快速叫车"]),
        ("/passenger/orders/new", "叫车页", ["立即叫车", "选择车型"]),
        ("/passenger/orders", "我的订单", ["我的订单"]),
        ("/passenger/addresses", "常用地址", ["常用地址"]),
        ("/passenger/profile", "个人资料", ["个人资料"]),
    ]:
        st, body = s.get(path)
        check(name + " " + path, st, body, 200, contains)

    # 已完成订单详情 + 轨迹
    st, body = s.get("/passenger/orders?status=completed")
    order_id = None
    if st == 200:
        import re
        m = re.search(r'/passenger/orders/(\d+)"', body)
        if m:
            order_id = m.group(1)

    if order_id:
        st, body = s.get("/passenger/orders/%s" % order_id)
        check("订单详情（已完成）", st, body, 200, ["状态流转时间线", "费用明细"])

        st, body = s.get("/passenger/orders/%s/track" % order_id)
        check("轨迹回放页", st, body, 200, ["轨迹"])
    else:
        FAIL.append(("找到乘客订单", 0, None))
        print("  [FAIL] 未能从订单列表中解析出订单 ID")

    # ---------- 司机 ----------
    print("\n[司机 d001]")
    s, st = login("d001", "123456")
    check("司机登录", st, "", 200)

    for path, name, contains in [
        ("/driver/", "司机工作台", ["工作台"]),
        ("/driver/pool", "待接单", ["待接单"]),
        ("/driver/orders", "我的行程", ["我的行程"]),
        ("/driver/vehicles", "我的车辆", ["我的车辆"]),
        ("/driver/reviews", "我的评价", ["我的评价"]),
        ("/driver/profile", "司机资料", ["司机资料"]),
    ]:
        st, body = s.get(path)
        check(name + " " + path, st, body, 200, contains)

    # ---------- 越权访问 ----------
    print("\n[权限校验]")
    s, st = login("p001", "123456")
    st, body = s.get("/admin/")
    check("乘客访问管理端被拦", st, body, 200, ["无权访问"])

    s, st = login("d001", "123456")
    st, body = s.get("/admin/drivers")
    check("司机访问管理端被拦", st, body, 200, ["无权访问"])

    # ---------- 错误页 ----------
    print("\n[错误处理]")
    st, body = s.get("/no-such-page-xyz")
    check("404 页面", st, body, 404, ["页面不存在"])

    # ---------- 汇总 ----------
    print("\n" + "=" * 72)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("\n失败清单：")
        for name, status, _ in FAIL:
            print("  - %s (状态码 %s)" % (name, status))
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
