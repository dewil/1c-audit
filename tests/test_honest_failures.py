"""Честные отказы и коды выхода. Спека: docs/dev/2026-09-30-spec-honest-failures.md
Инварианты: INV-AUDIT-10, INV-AUDIT-11, INV-AUDIT-14, INV-AUDIT-18.
"""
from __future__ import annotations

import pytest

from onec_audit import checks as checks_mod
from onec_audit.checks import ALL_CHECKS, Options
from onec_audit.cli import main
from onec_audit.report import Report

pytestmark = pytest.mark.honest_failures

CLEAN = "ничего требующего внимания не найдено"
BANK_CLEAN = "незавершенных сообщений, требующих внимания, нет"


def _summary(capsys, r):
    capsys.readouterr()
    r.summary()
    return capsys.readouterr().out


def _only_checks(monkeypatch, funcs):
    monkeypatch.setattr(checks_mod, "ALL_CHECKS", list(funcs))
    import onec_audit.cli as cli
    if hasattr(cli, "ALL_CHECKS"):
        monkeypatch.setattr(cli, "ALL_CHECKS", list(funcs))


def _bank_check():
    for c in ALL_CHECKS:
        if "bank" in c.__name__:
            return c
    pytest.fail("в ALL_CHECKS нет проверки обмена с банком (имя с 'bank')")


def test_ac1_clean_report_exit0_and_clean_phrase(capsys):
    """AC-1 / INV-AUDIT-10: нет находок и отказов -> 0 и фраза о чистоте."""
    r = Report(echo=True)
    assert r.exit_code() == 0
    assert CLEAN in _summary(capsys, r)


def test_ac2_findings_only_exit1():
    """AC-2 / INV-AUDIT-11: находки без отказов -> 1."""
    r = Report(echo=False)
    r.finding("что-то найдено")
    assert r.exit_code() == 1


@pytest.mark.parametrize("with_finding", [False, True])
def test_ac3_failure_exit4_summary(capsys, with_finding):
    """AC-3 / INV-AUDIT-10, INV-AUDIT-11: отказ -> 4, счетчик и имя, нет фразы о чистоте."""
    r = Report(echo=True)
    if with_finding:
        r.finding("находка до отказа")
    r.fail("моя_проверка", "запрос упал")
    assert r.exit_code() == 4
    out = _summary(capsys, r)
    assert "не выполнено проверок: 1" in out
    assert "моя_проверка" in out
    assert CLEAN not in out
    if with_finding:
        assert "находка до отказа" in out


def test_ac4_duplicate_fail_counted_once(capsys):
    """AC-4 / INV-AUDIT-10: повторный fail с тем же именем не увеличивает счетчик."""
    r = Report(echo=True)
    r.fail("check_a", "первая причина")
    r.fail("check_a", "вторая причина")
    assert len(r.failures) == 1
    assert "не выполнено проверок: 1" in _summary(capsys, r)


def test_ac3_two_distinct_failures_counted_two(capsys):
    """AC-3/AC-4 (граница) / INV-AUDIT-10: разные имена -> 2, оба в перечне."""
    r = Report(echo=True)
    r.fail("check_a", "x")
    r.fail("check_b", "y")
    out = _summary(capsys, r)
    assert "не выполнено проверок: 2" in out
    assert "check_a" in out and "check_b" in out


def test_ac5_bank_exchange_one_status_query_fails(fake_base, capsys):
    """AC-5 / INV-AUDIT-10: упал запрос по одному статусу -> отказ, без вердикта 'нет'."""
    target = fake_base.enums["СтатусыОбменСБанками"][0]
    fake_base.raise_when(
        lambda text, params: target in text or target in repr(params)
    )
    r = Report(echo=True)
    _bank_check()(fake_base, r, Options())
    assert len(r.failures) == 1, "проверка не зарегистрировала отказ"
    assert BANK_CLEAN not in capsys.readouterr().out


def test_ac5_bank_exchange_all_queries_fail(fake_base, capsys):
    """AC-5 / INV-AUDIT-10: все запросы падают -> отказ через Report.fail, без вердикта."""
    fake_base.raise_all = True
    r = Report(echo=True)
    _bank_check()(fake_base, r, Options())
    assert len(r.failures) == 1
    assert BANK_CLEAN not in capsys.readouterr().out


def test_bank_exchange_no_enum_is_failure(fake_base, capsys):
    """INV-AUDIT-10 (правило про перечисление): нет СтатусыОбменСБанками -> не выполнено."""
    fake_base.enums = {}
    r = Report(echo=True)
    _bank_check()(fake_base, r, Options())
    assert len(r.failures) == 1
    assert BANK_CLEAN not in capsys.readouterr().out


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda c: c.__name__)
def test_ac6_every_check_query_failure_is_reported(
    check, fake_base, patch_base, monkeypatch, capsys
):
    """AC-6 / INV-AUDIT-14, INV-AUDIT-18: любой запрос бросает -> отказ, 4, без падения."""
    _only_checks(monkeypatch, [check])
    fake_base.raise_all = True
    patch_base(fake_base)
    code = main(["--base", "x"])
    out = capsys.readouterr().out
    assert code == 4
    assert "не выполнено проверок: 1" in out
    assert CLEAN not in out


def test_ac6_check_exception_registered_with_check_name(
    fake_base, patch_base, monkeypatch, capsys
):
    """AC-6 / INV-AUDIT-14: исключение из проверки регистрируется с ее именем."""
    def boom_check(b, r, opt):
        raise ValueError("boom")

    _only_checks(monkeypatch, [boom_check])
    patch_base(fake_base)
    assert main(["--base", "x"]) == 4
    out = capsys.readouterr().out
    assert "не выполнено проверок: 1" in out
    assert "boom_check" in out


def test_ac7_srvr_without_ref_exit2(patch_base, capsys):
    """AC-7 / INV-AUDIT-18: --srvr без --ref -> 2, без traceback."""
    class Boom:
        def __getattr__(self, n):
            raise AssertionError("к базе обращаться нельзя")
    patch_base(Boom())
    assert main(["--srvr", "X"]) == 2
    cap = capsys.readouterr()
    assert "Traceback" not in cap.err + cap.out


def test_ac7_unknown_argument_exit2(patch_base):
    """AC-7 (У5) / INV-AUDIT-18: неизвестный флаг -> возврат 2, без SystemExit."""
    patch_base(object())
    assert main(["--no-such-flag"]) == 2


def test_ac7_help_returns_0(patch_base, capsys):
    """AC-7 (У5) / INV-AUDIT-18: --help -> возврат 0, без SystemExit."""
    patch_base(object())
    assert main(["--help"]) == 0
    assert capsys.readouterr().out.strip()


def test_ac8_config_synonym_read_fails_exit3(fake_base, patch_base, capsys):
    """AC-8 / INV-AUDIT-18: чтение config_synonym бросает -> 3."""
    fake_base.raise_on = {"config_synonym"}
    patch_base(fake_base)
    assert main(["--base", "x"]) == 3
    assert "Traceback" not in capsys.readouterr().err


def test_ac8_config_version_read_fails_exit3(fake_base, patch_base):
    """AC-8 / INV-AUDIT-18: чтение версии конфигурации бросает -> 3."""
    fake_base.raise_on = {"config_version"}
    patch_base(fake_base)
    assert main(["--base", "x"]) == 3


def test_ac8_connect_error_exit3(monkeypatch, capsys):
    """AC-8 / INV-AUDIT-18: ошибка подключения -> 3."""
    def failing(*a, **k):
        raise RuntimeError("не подключиться")
    monkeypatch.setattr("onec_audit.cli.Base", failing)
    assert main(["--base", "x"]) == 3
    assert "Traceback" not in capsys.readouterr().err


def test_ac9_out_unwritable_exit4(fake_base, patch_base, tmp_path, capsys):
    """AC-9 / INV-AUDIT-18: --out в несуществующий путь -> 4, отчет напечатан, stderr."""
    patch_base(fake_base)
    bad = tmp_path / "нет" / "такой" / "папки" / "report.txt"
    code = main(["--base", "x", "--out", str(bad)])
    cap = capsys.readouterr()
    assert code == 4
    assert cap.out.strip(), "отчет должен быть напечатан до ошибки сохранения"
    assert cap.err.strip(), "ожидалось сообщение в stderr"
    assert "Traceback" not in cap.err


def test_ac10_all_checks_ok_empty_data_exit0(fake_base, patch_base, capsys):
    """AC-10 / INV-AUDIT-11: все проверки выполнены на пустых данных -> 0."""
    patch_base(fake_base)
    assert main(["--base", "x"]) == 0
    out = capsys.readouterr().out
    assert "не выполнено проверок" not in out
    assert CLEAN in out


def test_ac10_finding_exit1(fake_base, patch_base, monkeypatch):
    """AC-10 / INV-AUDIT-11: выполнено, есть находка -> 1."""
    def finds(b, r, opt):
        r.finding("находка")
    _only_checks(monkeypatch, [finds])
    patch_base(fake_base)
    assert main(["--base", "x"]) == 1


def test_ac10_failure_beats_finding(fake_base, patch_base, monkeypatch):
    """AC-3/AC-10 / INV-AUDIT-11: отказ приоритетнее находок -> 4."""
    def finds(b, r, opt):
        r.finding("находка")

    def boom(b, r, opt):
        raise RuntimeError("x")
    _only_checks(monkeypatch, [finds, boom])
    patch_base(fake_base)
    assert main(["--base", "x"]) == 4


def test_ac11_status_missing_in_metadata_is_skipped_not_failed(fake_base, capsys):
    """AC-11 / INV-AUDIT-10: нет статуса в метаданных -> проверка выполнена, пометка о пропуске."""
    fake_base.enums = {"СтатусыОбменСБанками": ["ОтклоненБанком", "Сформирован"]}
    r = Report(echo=True)
    _bank_check()(fake_base, r, Options())
    out = capsys.readouterr().out
    assert len(r.failures) == 0
    assert "пропущен" in out
    assert "Приостановлен" in out  # У2: имя пропущенного статуса
