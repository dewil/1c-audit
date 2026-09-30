"""Подключение. Спека: docs/dev/2026-09-30-spec-runtime.md, часть 1.
Инварианты: INV-AUDIT-01, INV-AUDIT-14, INV-AUDIT-15, INV-AUDIT-18.
"""
from __future__ import annotations

import pytest

from onec_audit.report import Report

pytestmark = pytest.mark.connection

NEW_LINE = "Данные учёта не изменялись: только запросы на чтение."


class Reg:
    def __init__(self, p32=None, p64=None):
        self.paths = {32: p32, 64: p64}
        self.asked = []

    def inproc_path(self, view_bits):
        self.asked.append(view_bits)
        return self.paths[view_bits]


def test_ac1_64_process_path_in_64():
    """AC-1 / INV-AUDIT-18: 64 бит, путь в 64 -> без ошибки."""
    from onec_audit.base import check_bitness
    check_bitness(Reg(p64=r"C:\1cv8\comcntr.dll"), process_bits=64)


def test_ac2_64_process_path_only_in_32():
    """AC-2 / INV-AUDIT-18: 64 бит, путь только в 32 -> BitnessError про 32-битный."""
    from onec_audit.base import BitnessError, check_bitness
    with pytest.raises(BitnessError) as e:
        check_bitness(Reg(p32=r"C:\1cv8\comcntr.dll"), process_bits=64)
    assert "32-бит" in str(e.value)


def test_ac3_32_process_path_in_32():
    """AC-3 / INV-AUDIT-18: 32 бит (подмена), путь в 32 -> без ошибки."""
    from onec_audit.base import check_bitness
    check_bitness(Reg(p32=r"C:\1cv8\comcntr.dll"), process_bits=32)


def test_ac4_no_path_anywhere():
    """AC-4 / INV-AUDIT-18: пути нет нигде -> BitnessError с regsvr32."""
    from onec_audit.base import BitnessError, check_bitness
    with pytest.raises(BitnessError) as e:
        check_bitness(Reg(), process_bits=64)
    assert "regsvr32" in str(e.value)


def test_ac5_main_bitness_error_exit5(monkeypatch):
    """AC-5 / INV-AUDIT-18: BitnessError в main -> код 5."""
    from onec_audit.base import BitnessError
    from onec_audit.cli import main

    def failing(*a, **k):
        raise BitnessError("коннектор зарегистрирован как 32-битный")
    monkeypatch.setattr("onec_audit.cli.Base", failing)
    assert main(["--base", "x"]) == 5


def _bare_base():
    from onec_audit.base import Base
    b = Base.__new__(Base)
    b.c = object()
    b.md = object()
    return b


def test_ac6_close_releases_both_and_is_idempotent():
    """AC-6 / INV-AUDIT-14: close() обнуляет c и md, повтор безопасен."""
    b = _bare_base()
    b.close()
    assert b.c is None and b.md is None
    b.close()


def test_ac6_context_manager_closes():
    """AC-6 / INV-AUDIT-14: __exit__ вызывает close()."""
    b = _bare_base()
    with b as inner:
        assert inner is b
    assert b.c is None and b.md is None


def test_ac7_last_report_line(capsys):
    """AC-7 / INV-AUDIT-01: последняя строка отчета - новая формулировка."""
    r = Report(echo=True)
    capsys.readouterr()
    r.summary()
    lines = [x for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert lines[-1].strip() == NEW_LINE
    assert "НИЧЕГО" not in "\n".join(lines)
