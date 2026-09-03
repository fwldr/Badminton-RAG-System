"""pytest 全局夹具：离线后端 + 限流器隔离。

- 强制 DB_BACKEND=sqlite：无论 `.env`/环境变量如何配置（如本机开了 MySQL），
  测试一律走内存/临时文件的 SQLite，绝不连真实数据库（CLAUDE.md 离线约定）。
- 令牌桶是进程级单例（app.api.deps._ask_limiter/_admin_limiter），跨测试共享会让
  请求量大的测试把桶打空触发 429。每个测试前后重建，保证测试互不干扰。
"""

import os

# 必须在导入任何 app 模块之前设置（get_settings() 为 lru_cache 单例，环境变量优先于 .env）
os.environ["DB_BACKEND"] = "sqlite"

import pytest

from app.api import deps


@pytest.fixture(autouse=True)
def _fresh_limiters():
    deps.reset_limiters()
    yield
    deps.reset_limiters()
