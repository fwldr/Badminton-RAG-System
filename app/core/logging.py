import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import Settings, get_settings

_configured = False


def setup_logging(settings: Settings | None = None) -> None:
    """初始化根日志：控制台 + 滚动文件，幂等（重复调用直接返回）。"""
    global _configured
    if _configured:
        return

    settings = settings or get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    log_dir: Path = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    _configured = True
