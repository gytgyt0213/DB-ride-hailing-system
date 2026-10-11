# -*- coding: utf-8 -*-
"""集中配置：数据库连接、会话参数等。

读取优先级：环境变量 > 项目根目录的 .env 文件 > 内置默认值。
刻意只依赖标准库（手写了一个极简 .env 解析器），
避免为一个配置文件引入 python-dotenv 依赖。

注意：本项目运行于 Python 3.9，不使用 3.10+ 语法。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent
SQL_DIR = BASE_DIR / "sql"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DOCS_DIR = BASE_DIR / "docs"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """极简 .env 解析：支持 KEY=VALUE、# 注释、引号包裹的值。"""
    result: Dict[str, str] = {}
    if not path.is_file():
        return result

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 去掉成对的引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


_ENV_FILE = _parse_env_file(BASE_DIR / ".env")


def get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """环境变量优先，其次 .env 文件，最后默认值。"""
    value = os.environ.get(key)
    if value is not None and value != "":
        return value
    return _ENV_FILE.get(key, default)


class DBConfig(object):
    """数据库连接参数。"""

    host = get_env("DB_HOST", "127.0.0.1")
    port = int(get_env("DB_PORT", "3306") or "3306")
    user = get_env("DB_USER", "root")
    password = get_env("DB_PASSWORD", "")
    database = get_env("DB_NAME", "taxi_system")
    charset = "utf8mb4"

    @classmethod
    def as_dict(cls) -> Dict[str, object]:
        return {
            "host": cls.host,
            "port": cls.port,
            "user": cls.user,
            "password": cls.password,
            "database": cls.database,
            "charset": cls.charset,
        }

    @classmethod
    def describe(cls) -> str:
        return "%s@%s:%s/%s" % (cls.user, cls.host, cls.port, cls.database)


class SessionConfig(object):
    """服务端会话参数。"""

    # 会话有效期（秒），默认 8 小时
    lifetime_seconds = int(get_env("SESSION_LIFETIME", "28800") or "28800")
    cookie_name = get_env("SESSION_COOKIE", "taxi_session") or "taxi_session"


class AppConfig(object):
    app_name = "网约车（打车）系统"
    # ⚠️ 本机 PATH 上的 python 不是 3.9，运行请显式使用：
    #    F:\pythonnewversion\py39ver\python.exe
    python_exe = get_env("PYTHON_EXE", r"F:\pythonnewversion\py39ver\python.exe")
    debug = (get_env("APP_DEBUG", "0") or "0") == "1"
