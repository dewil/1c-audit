# -*- coding: utf-8 -*-
"""Командная строка: прогон всех проверок и печать отчёта."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from .base import Base, BitnessError, ConnectError
from .checks import ALL_CHECKS, Options
from .report import Report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="onec-audit",
        description="Диагностика базы 1С:Предприятие. Только чтение: "
                    "ничего не создаётся, не записывается и не проводится.",
    )
    src = p.add_argument_group("подключение")
    src.add_argument("--base", default=os.environ.get("ONEC_BASE"),
                     help="каталог файловой базы (или переменная ONEC_BASE)")
    src.add_argument("--srvr", default=os.environ.get("ONEC_SRVR"),
                     help="сервер 1С для клиент-серверной базы (или ONEC_SRVR)")
    src.add_argument("--ref", default=os.environ.get("ONEC_REF"),
                     help="имя базы на сервере (или ONEC_REF)")
    src.add_argument("--user", default=os.environ.get("ONEC_USER"),
                     help="пользователь 1С (или ONEC_USER). "
                          "Пароль — только через ONEC_PWD, в аргументах он виден "
                          "в списке процессов")

    tune = p.add_argument_group("настройка проверок")
    tune.add_argument("--since-year", type=int, default=dt.date.today().year - 2,
                      help="с какого года смотреть обмен с банком")
    tune.add_argument("--locked-before", type=int, default=0,
                      help="год, до которого правки запрещены; находки раньше "
                           "этой границы не идут в сводку (0 — всё изменяемо)")
    tune.add_argument("--all-docs", action="store_true",
                      help="сканировать все типы документов, а не короткий список")

    out = p.add_argument_group("вывод")
    out.add_argument("--out", help="сохранить отчёт в файл")
    out.add_argument("--quiet", action="store_true",
                     help="не печатать в консоль (осмысленно вместе с --out)")
    return p


def main(argv: list[str] | None = None) -> int:
    # консоль Windows (cp1251) не умеет часть символов отчёта, например «−»
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)

    if not args.base and not args.srvr:
        print("STOP: укажите --base (файловая база) либо --srvr и --ref.\n"
              "      Можно задать переменными окружения ONEC_BASE / ONEC_SRVR+ONEC_REF.",
              file=sys.stderr)
        return 2

    report = Report(echo=not args.quiet)
    try:
        base = Base(args.base, srvr=args.srvr, ref=args.ref, user=args.user)
    except BitnessError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    except ConnectError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3

    options = Options(
        since_year=args.since_year,
        locked_before=args.locked_before,
        all_docs=args.all_docs,
    )

    started = dt.datetime.now()
    report.rule("=")
    report.w(f"ДИАГНОСТИКА 1С — {started:%Y-%m-%d %H:%M}")
    report.rule("=")
    report.w(f"База        : {base.description}")
    report.w(f"Конфигурация: {base.config_synonym}  {base.config_version}")
    report.w(f"Платформа   : {base.platform_version()}")

    for check in ALL_CHECKS:
        try:
            check(base, report, options)
        except Exception as exc:  # одна упавшая проверка не должна ронять отчёт
            report.w()
            report.w(f"   проверка {check.__name__} не выполнена: {exc}")

    report.summary()
    report.w()
    report.w(f"Время выполнения: {(dt.datetime.now() - started).total_seconds():.0f} с. "
             "Записано в базу: НИЧЕГО.")

    if args.out:
        report.save(args.out)
        if not args.quiet:
            print(f"\nОтчёт сохранён: {args.out}")

    base.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
