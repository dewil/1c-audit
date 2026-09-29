# -*- coding: utf-8 -*-
"""onec-audit — чтение и диагностика базы 1С:Предприятие через COM.

Только чтение. Ни один публичный метод библиотеки не изменяет базу:
доступны Запрос.Выполнить() и обход метаданных, и ничего больше.
"""
from .base import (
    Base,
    BitnessError,
    ConnectError,
    build_connection_string,
    check_bitness,
    datetime_literal,
)
from .checks import ALL_CHECKS, Options
from .report import Report, money

__version__ = "0.1.0"

__all__ = [
    "Base",
    "BitnessError",
    "ConnectError",
    "build_connection_string",
    "check_bitness",
    "datetime_literal",
    "ALL_CHECKS",
    "Options",
    "Report",
    "money",
    "__version__",
]
