"""Rotating file log. The OAuth token must never reach this."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import LOG_FILE, ensure_dirs

_configured = False


def setup(verbose: bool = False) -> logging.Logger:
    global _configured
    logger = logging.getLogger("clausage")
    if _configured:
        return logger
    ensure_dirs()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    if verbose:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    logger.propagate = False
    _configured = True
    return logger


def get(name: str = "") -> logging.Logger:
    base = logging.getLogger("clausage")
    return base.getChild(name) if name else base
