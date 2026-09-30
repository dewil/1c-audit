# -*- coding: utf-8 -*-
"""Командная строка: прогон всех проверок и печать отчёта."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

from .base import Base, BitnessError
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


def _close(base) -> None:
    try:
        base.close()
    except Exception as exc:  # закрытие не меняет результат прогона
        print(f"ПРЕДУПРЕЖДЕНИЕ: не удалось закрыть соединение: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # консоль Windows (cp1251) не умеет часть символов отчёта, например «−»
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse выходит сам: отдаём его код, не бросаем
        return exc.code if isinstance(exc.code, int) else 2

    if (not args.base and not args.srvr) or (args.srvr and not args.ref):
        print("STOP: укажите --base (файловая база) либо --srvr и --ref.\n"
              "      Можно задать переменными окружения ONEC_BASE / ONEC_SRVR+ONEC_REF.",
              file=sys.stderr)
        return 2

    report = Report(echo=not args.quiet)
    try:
        base = Base(args.base, srvr=args.srvr, ref=args.ref, user=args.user)
    except (BitnessError, ValueError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3

    try:  # шапку читаем до печати: при ошибке отчёт не начинается
        config = f"{base.config_synonym}  {base.config_version}"
        platform = base.platform_version()
    except Exception as exc:
        print(f"STOP: не удалось прочитать шапку базы: {exc}", file=sys.stderr)
        _close(base)
        return 3

    options = Options(
        since_year=args.since_year,
        locked_before=args.locked_before,
        all_docs=args.all_docs,
    )

    code = 4  # что бы ни случилось после начала отчёта, это не «чисто»
    try:
        started = dt.datetime.now()
        report.rule("=")
        report.w(f"ДИАГНОСТИКА 1С — {started:%Y-%m-%d %H:%M}")
        report.rule("=")
        report.w(f"База        : {base.description}")
        report.w(f"Конфигурация: {config}")
        report.w(f"Платформа   : {platform}")

        for check in ALL_CHECKS:
            try:
                check(base, report, options)
            except Exception as exc:  # одна упавшая проверка не должна ронять отчёт
                report.w()
                report.fail(check.__name__, str(exc))

        report.summary()
        report.w()
        report.w(f"Время выполнения: {(dt.datetime.now() - started).total_seconds():.0f} с. "
                 "Записано в базу: НИЧЕГО.")

        code = report.exit_code()
        if args.out:
            try:
                report.save(args.out)
                if not args.quiet:
                    print(f"\nОтчёт сохранён: {args.out}")
            except Exception as exc:
                print(f"STOP: не удалось сохранить отчёт: {exc}", file=sys.stderr)
                code = 4
    except Exception as exc:
        print(f"STOP: сбой при выводе отчёта: {exc}", file=sys.stderr)

    _close(base)
    return code


if __name__ == "__main__":
    sys.exit(main())
