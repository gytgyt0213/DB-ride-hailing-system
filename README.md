# 网约车（打车）系统

数据库课程大作业。一个简化的城市网约车平台，覆盖「乘客下单 → 平台派单 → 司机接单 → 行程执行 → 订单状态追踪 → 计价」完整链路。

技术栈：**MySQL 8.4 + FastAPI + 手写 SQL（pymysql）+ Jinja2 服务端渲染**。
刻意**不使用 ORM** —— 本作业的核心是数据库设计与 SQL 能力，所有查询均为手写参数化 SQL。

---

## ⚠️ 运行前必读：Python 解释器

本机的 `PATH` 上的 `python` **不是**本项目要用的版本。所有命令必须显式使用：

```
F:\pythonnewversion\py39ver\python.exe
```

该环境为 **Python 3.9.23**，依赖已安装在此。用错解释器会出现「模块找不到」或语法报错。

---

## 一、环境准备

### 1. 依赖安装

```bash
F:\pythonnewversion\py39ver\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 2. 数据库配置

复制 `.env.example` 为 `.env`，填入本机 MySQL 的实际连接信息：

```ini
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=你的密码
DB_NAME=taxi_system
```

MySQL 需为 **8.x**（本项目使用了 CHECK 约束、生成列、窗口函数、CTE 等 8.0+ 特性）。

---

## 二、初始化数据库

**按序号依次执行** `sql/` 下的脚本：

```bash
cd /e/DB_Homework/DB_System
for f in 00_create_database 01_create_tables 02_indexes 03_views 04_triggers_procedures 05_seed_base 06_seed_demo; do
  mysql -u root -p --default-character-set=utf8mb4 < sql/$f.sql
done
```

| 脚本 | 作用 |
| --- | --- |
| `00_create_database.sql` | 建库 `taxi_system`（utf8mb4） |
| `01_create_tables.sql` | 16 张表（含约束、外键、生成列） |
| `02_indexes.sql` | 二级索引（**仅可执行一次**） |
| `03_views.sql` | 6 个视图 |
| `04_triggers_procedures.sql` | 存储函数/过程/触发器（**必须用 mysql CLI 执行**，见下） |
| `05_seed_base.sql` | 角色、车型、管理员、计价规则 |
| `06_seed_demo.sql` | 演示数据（900 订单等） |
| `07_queries_demo.sql` | 报告用演示查询（可选，不改变数据） |
| `99_drop_all.sql` | **清空全部对象**，用于重置 |

> **为什么 `04` 必须用 mysql 命令行客户端？**
> 存储过程和触发器里含 `BEGIN...END` 与分号，需要 `DELIMITER` 指令。
> `DELIMITER` 是 mysql **客户端**指令而非服务器 SQL，pymysql 逐条发送语句、不认识它，
> 会因分号提前截断而报语法错误。因此 `04` 只能用 CLI 执行。

> **重置数据库**：执行 `99_drop_all.sql` 后，从 `00` 重新按序执行即可。

---

## 三、启动应用

```bash
cd /e/DB_Homework/DB_System
F:/pythonnewversion/py39ver/python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 <http://127.0.0.1:8000>。

> `uvicorn.exe` 未加入 `PATH`，因此用 `python -m uvicorn` 方式启动。

---

## 四、演示账号

| 角色 | 账号 | 密码 | 入口 |
| --- | --- | --- | --- |
| 平台管理员 | `admin` | `admin123` | `/admin` |
| 司机 | `d001` ~ `d030` | `123456` | `/driver` |
| 乘客 | `p001` ~ `p050` | `123456` | `/passenger` |

乘客可自助注册新账号（`/auth/register`）；司机账号由管理员创建。

---

## 五、目录结构

```
DB_System/
├─ main.py                  FastAPI 入口
├─ config.py                集中配置（环境变量 > .env > 默认值）
├─ db/                      连接与事务管理
│   ├─ connection.py        pymysql 连接（每请求独立连接）
│   └─ transaction.py       事务上下文管理器
├─ security/                认证与安全
│   ├─ password.py          PBKDF2-HMAC-SHA256 哈希
│   └─ session.py           服务端会话（DB 存储）
├─ repositories/            纯 SQL 数据访问层
├─ services/                业务编排（事务边界、状态机、并发控制）
├─ routers/                 路由（按角色划分）
├─ templates/               Jinja2 模板
├─ static/                  CSS / JS
├─ sql/                     数据库脚本（00 ~ 99）
├─ scripts/                 数据生成与测试脚本
│   ├─ gen_fake_data.py     演示数据生成器
│   └─ concurrency_test.py  并发接单测试
├─ docs/                    ER 图、设计说明、实验报告
└─ tests/                   单元测试
```

---

## 六、测试

四个测试套件，可任意顺序运行：

```bash
cd /e/DB_Homework/DB_System

# 1. 单元测试：密码哈希（不需要数据库）
F:/pythonnewversion/py39ver/python.exe -m unittest discover -s tests -v

# 2. 并发接单测试：5 线程同时抢单
F:/pythonnewversion/py39ver/python.exe scripts/concurrency_test.py

# 3. 页面与权限冒烟测试：遍历三端 35 个页面（需先启动应用）
F:/pythonnewversion/py39ver/python.exe scripts/smoke_web.py

# 4. 端到端业务流程测试：下单→派单→接单→到达→行程→结算→评价（需先启动应用）
F:/pythonnewversion/py39ver/python.exe scripts/smoke_flow.py
```

后三个脚本会向数据库写入测试数据。跑完后如需恢复干净的演示数据，
执行 `sql/99_drop_all.sql` 再从 `00` 按序重建即可。

### 并发接单测试

`scripts/concurrency_test.py` 用 5 个线程、各自独立的数据库连接，
在**同一瞬间**抢同一订单，验证三件事：

1. 有且仅有 1 人接单成功；
2. 订单最终状态与状态日志保持一致；
3. 数据库层唯一索引 `uk_active_driver` 能阻止「一个司机同时接两单」。

加 `--cleanup` 可清理本次测试数据。

> **为什么必须用多线程测试？**
> 顺序调用接单过程是验证不到并发问题的：第一个司机成功后，其他司机的邀请会被置为
> `cancelled`，第二个司机在邀请校验阶段就被拦下，根本走不到那句关键的
> `UPDATE ... WHERE status='pending'`。只有让多个线程用**各自独立的连接**同时冲进去，
> 才能真正压到并发守卫上。

---

## 七、脚本说明

### 生成演示数据

```bash
F:/pythonnewversion/py39ver/python.exe scripts/gen_fake_data.py --reset
```

固定随机种子，结果可复现。规模参数在脚本开头的常量中调整。

### 刷新演示数据的时间戳

演示数据的时间戳是**生成那一刻**写死的。隔几天再打开，进行中的订单会显示成
「已行程中好几天」，看起来不真实。

演示或答辩前二选一：

```bash
# 方式 A：只平移时间戳（推荐，不到 1 秒，数据一行不动）
F:/pythonnewversion/py39ver/python.exe scripts/refresh_demo_time.py

# 方式 B：完全重新生成（约 13 秒，数据重写）
F:/pythonnewversion/py39ver/python.exe scripts/gen_fake_data.py --reset
```

**推荐方式 A** —— 它只把所有「事件时间列」（订单时间戳、状态日志、派单邀请、
轨迹点、评价时间、车辆定位）整体前移，业务数据完全不变。
`--dry-run` 可以只预览偏移量而不实际修改。

由于生成器是确定性的，两种方式得到的数据等价。方式 A 适用于「只想让时间看起来是刚刚」。

---

## 八、数据库设计要点

| 设计点 | 方案 |
| --- | --- |
| 订单状态与时间 | 主表冗余状态时间戳列（服务 UI）**＋** 独立状态日志表（服务审计），同事务双写 |
| 「车辆可运营才能接单」跨表约束 | MySQL CHECK 不能跨表 → 视图筛资格 + 事务内 `FOR UPDATE` 复检 + 条件更新防并发 |
| 计价规则变更 | 版本化：只新增版本行，绝不 UPDATE 已生效的单价列 |
| 历史订单金额不变 | 下单时把规则价格**快照**进订单行 |
| 派单与拒单 | 独立 `order_dispatch` 表，`(order_id, driver_id)` 唯一 |
| 一司一单 | 生成列 `active_driver_id` + 唯一索引 `uk_active_driver`，数据库层兜底 |
| 两个司机抢同一单 | `UPDATE ... WHERE status='pending'` + 判断 `ROW_COUNT()`，行锁天然串行化 |

详见 `docs/数据库设计说明.md`。
