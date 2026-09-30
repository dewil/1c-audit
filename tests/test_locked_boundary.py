"""Граница закрытого периода. Спека: docs/dev/2026-09-30-spec-locked-boundary.md
Инварианты: INV-AUDIT-03, INV-AUDIT-04, INV-AUDIT-10, INV-AUDIT-18, INV-AUDIT-80.
"""
from __future__ import annotations

import types
from datetime import date, datetime, timedelta, timezone

import pytest

from onec_audit import checks as checks_mod
from onec_audit.checks import Options
from onec_audit.cli import main
from onec_audit.report import Report

pytestmark = pytest.mark.locked_boundary

CONST = "ИспользоватьДатыЗапретаИзменения"
REG = "ДатыЗапретаИзменения"


def resolve_boundary(b, r, opt):
    from onec_audit.checks import resolve_boundary as f  # У6: импорт внутри
    return f(b, r, opt)


def _setup(fake, *, const=True, reg=True, const_value=True,
           reg_date=datetime(2024, 12, 31), record=True, enum=True):
    """Подменная база с константой и регистром. Правило константы - первым
    (ее имя содержит имя регистра как подстроку)."""
    if enum:  # С1: имя перечисления проверяется по метаданным
        fake.enums["ВидыНазначенияДатЗапрета"] = ["ДляВсехПользователей", "ДляВсехИнформационныхБаз"]
    fake.names_by_kind["Константы"] = [CONST] if const else []
    fake.names_by_kind["РегистрыСведений"] = [REG] if reg else []
    fake.on(CONST, types.SimpleNamespace(Значение=const_value))
    fake.on(REG, types.SimpleNamespace(ДатаЗапрета=reg_date) if record else None)
    return fake


def _only_checks(monkeypatch, funcs):
    monkeypatch.setattr(checks_mod, "ALL_CHECKS", list(funcs))
    import onec_audit.cli as cli
    if hasattr(cli, "ALL_CHECKS"):
        monkeypatch.setattr(cli, "ALL_CHECKS", list(funcs))


def _run(fake, patch_base, monkeypatch, capsys, argv=(), checks=()):
    """main с заданным набором проверок; возвращает (код, вывод, locked_before из проверки-шпиона)."""
    seen = []

    def spy(b, r, opt):
        seen.append(opt.locked_before)

    _only_checks(monkeypatch, [spy, *checks])
    patch_base(fake)
    code = main(["--base", "x", *argv])
    return code, capsys.readouterr().out, seen


def _boundary_queries(fake):
    return [q for q in fake.queries if REG in q or CONST in q]


# ---------- AC-1 ----------
@pytest.mark.parametrize("arg", ["2025-01-01", "2025"])
def test_ac1_param_gives_date_and_no_db_query(arg, fake_base, patch_base, monkeypatch, capsys):
    """AC-1 / INV-AUDIT-04: параметр (дата или год) -> 2025-01-01, источник 'параметр', база не опрашивается."""
    _setup(fake_base)
    _, out, seen = _run(fake_base, patch_base, monkeypatch, capsys, ["--locked-before", arg])
    assert seen == [date(2025, 1, 1)]
    assert "Граница     : 01.01.2025 (параметр)" in out
    assert _boundary_queries(fake_base) == []


def test_ac1_param_beats_db(fake_base, patch_base, monkeypatch, capsys):
    """AC-1 / INV-AUDIT-04: параметр приоритетнее даты из базы."""
    _setup(fake_base, reg_date=datetime(2023, 12, 31))
    _, _, seen = _run(fake_base, patch_base, monkeypatch, capsys, ["--locked-before", "2025-01-01"])
    assert seen == [date(2025, 1, 1)]


def test_ac1_resolve_boundary_with_param_does_not_query(fake_base):
    """AC-1 / INV-AUDIT-04: resolve_boundary при заданном locked_before не трогает базу и значение."""
    _setup(fake_base)
    opt = Options(locked_before=date(2025, 3, 15))
    resolve_boundary(fake_base, Report(echo=False), opt)
    assert opt.locked_before == date(2025, 3, 15)
    assert fake_base.queries == []


# ---------- AC-2 ----------
def test_ac2_db_date_plus_one_day(fake_base):
    """AC-2 / INV-AUDIT-04: константа истинна, дата 2024-12-31 -> граница 2025-01-01."""
    _setup(fake_base)
    opt = Options()
    r = Report(echo=False)
    resolve_boundary(fake_base, r, opt)
    assert opt.locked_before == date(2025, 1, 1)
    assert len(r.failures) == 0


def test_ac2_main_takes_boundary_from_db(fake_base, patch_base, monkeypatch, capsys):
    """AC-2 / INV-AUDIT-04: main до проверок берет границу из базы; отказа нет."""
    _setup(fake_base)
    code, _, seen = _run(fake_base, patch_base, monkeypatch, capsys)
    assert seen == [date(2025, 1, 1)]
    assert code == 0


def test_ac2_default_options_locked_before_none():
    """AC-2 / INV-AUDIT-04: Options().locked_before по умолчанию None."""
    assert Options().locked_before is None


# ---------- AC-3 ----------
_NO_BOUNDARY = {
    "const_false": dict(const_value=False),
    "no_const": dict(const=False),
    "no_register": dict(reg=False),
    "no_record": dict(record=False),
    "date_none": dict(reg_date=None),
    "date_1c_empty": dict(reg_date=datetime(1, 1, 1)),
}


@pytest.mark.parametrize("kw", list(_NO_BOUNDARY.values()), ids=list(_NO_BOUNDARY.keys()))
def test_ac3_resolve_no_boundary_no_failure(kw, fake_base):
    """AC-3 / INV-AUDIT-04: границы нет, отказа нет."""
    _setup(fake_base, **kw)
    opt = Options()
    r = Report(echo=False)
    resolve_boundary(fake_base, r, opt)
    assert opt.locked_before is None
    assert len(r.failures) == 0


@pytest.mark.parametrize("kw", list(_NO_BOUNDARY.values()), ids=list(_NO_BOUNDARY.keys()))
def test_ac3_main_not_set_source(kw, fake_base, patch_base, monkeypatch, capsys):
    """AC-3 / INV-AUDIT-04: в main источник 'не задана', код 0, без отказов."""
    _setup(fake_base, **kw)
    code, out, seen = _run(fake_base, patch_base, monkeypatch, capsys)
    assert seen == [None]
    assert code == 0
    assert "Граница     : не задана" in out
    assert "не выполнено проверок" not in out


# ---------- AC-4 ----------
def _failing_boundary_base(fake):
    _setup(fake)
    fake.rules.clear()
    fake.raise_when(REG)  # подстрока покрывает и запрос константы
    return fake


def test_ac4_resolve_failure_registered(fake_base):
    """AC-4 / INV-AUDIT-10: падение запроса -> r.fail('locked_boundary'), границы нет."""
    _failing_boundary_base(fake_base)
    opt = Options()
    r = Report(echo=False)
    resolve_boundary(fake_base, r, opt)
    assert opt.locked_before is None
    assert len(r.failures) == 1
    assert "locked_boundary" in str(r.failures)


def test_ac4_main_exit4_and_other_checks_run(fake_base, patch_base, monkeypatch, capsys):
    """AC-4 / INV-AUDIT-10, 18: отказ -> код 4, остальные проверки выполняются без границы."""
    _failing_boundary_base(fake_base)
    ran = []

    def other(b, r, opt):
        ran.append(opt.locked_before)

    code, out, seen = _run(fake_base, patch_base, monkeypatch, capsys, checks=[other])
    assert code == 4
    assert ran == [None] and seen == [None]
    assert "locked_boundary" in out
    assert "не выполнено проверок: 1" in out


# ---------- AC-5 ----------
def test_ac5_header_db(fake_base, patch_base, monkeypatch, capsys):
    """AC-5 / INV-AUDIT-04: шапка '(дата запрета в базе)'."""
    _setup(fake_base)
    _, out, _ = _run(fake_base, patch_base, monkeypatch, capsys)
    assert "Граница     : 01.01.2025 (дата запрета в базе)" in out


def test_ac5_header_param(fake_base, patch_base, monkeypatch, capsys):
    """AC-5 / INV-AUDIT-04: шапка '(параметр)'."""
    _, out, _ = _run(fake_base, patch_base, monkeypatch, capsys, ["--locked-before", "2025"])
    assert "Граница     : 01.01.2025 (параметр)" in out


def test_ac5_header_not_set(fake_base, patch_base, monkeypatch, capsys):
    """AC-5 / INV-AUDIT-04: шапка 'не задана' без даты и без скобок источника."""
    _, out, _ = _run(fake_base, patch_base, monkeypatch, capsys)
    line = next(ln for ln in out.splitlines() if ln.startswith("Граница"))
    assert line.rstrip() == "Граница     : не задана"


# ---------- AC-6 / AC-7 (через is_locked / months_to_check, У1) ----------
BOUNDARY = date(2025, 1, 1)


@pytest.mark.parametrize("moment,expected", [
    (datetime(2024, 12, 31, 23, 59), True),
    (datetime(2025, 1, 1, 0, 0), False),
    (date(2024, 12, 31), True),
    (date(2025, 1, 1), False),
    (datetime(2024, 12, 31, 23, 59, tzinfo=timezone.utc), True),
    (datetime(2025, 1, 1, 0, 0, tzinfo=timezone(timedelta(hours=-5))), False),
    (datetime(2024, 12, 31, 23, 59, tzinfo=timezone(timedelta(hours=3))), True),
])
def test_ac6_is_locked_by_date(moment, expected):
    """AC-6 / INV-AUDIT-03, INV-AUDIT-04: строго раньше границы закрыт; сравнение по календарной дате."""
    from onec_audit.checks import is_locked
    assert is_locked(moment, Options(locked_before=BOUNDARY)) is expected


@pytest.mark.parametrize("moment", [datetime(2000, 1, 1), datetime(2024, 12, 31, 23, 59), date(2025, 1, 1)])
def test_ac6_is_locked_no_boundary_always_false(moment):
    """AC-6 / INV-AUDIT-03: без границы ничто не закрыто."""
    from onec_audit.checks import is_locked
    assert is_locked(moment, Options()) is False


def test_ac7_months_start_at_boundary_month():
    """AC-7 / INV-AUDIT-80: граница 2025-03-15 -> список с (2025, 3), до последнего завершенного месяца."""
    from onec_audit.checks import months_to_check
    ms = months_to_check(Options(locked_before=date(2025, 3, 15)), date(2025, 6, 10))
    assert ms[0] == (2025, 3)
    assert ms[-1] == (2025, 5)
    assert ms == [(2025, 3), (2025, 4), (2025, 5)]


def test_ac7_months_without_boundary_start_last_year_january():
    """AC-7 / INV-AUDIT-80: без границы - с января прошлого года."""
    from onec_audit.checks import months_to_check
    ms = months_to_check(Options(), date(2025, 6, 10))
    assert ms[0] == (2024, 1)
    assert ms[-1] == (2025, 5)
    assert len(ms) == 17


# ---------- AC-8 ----------
def test_ac8_options_default_since_year():
    """AC-8 / INV-AUDIT-80: Options().since_year == текущий год - 2."""
    assert Options().since_year == date.today().year - 2


def test_ac8_cli_default_since_year(fake_base, patch_base, monkeypatch):
    """AC-8 / INV-AUDIT-80: CLI без --since-year дает то же значение."""
    got = []

    def spy(b, r, opt):
        got.append(opt.since_year)

    _only_checks(monkeypatch, [spy])
    patch_base(fake_base)
    main(["--base", "x"])
    assert got == [date.today().year - 2]


# ---------- AC-9 ----------
class _Boom:
    def __getattr__(self, n):
        raise AssertionError("к базе обращаться нельзя")


@pytest.mark.parametrize("argv", [
    ["--locked-before", (date.today() + timedelta(days=1)).isoformat()],
    ["--locked-before", str(date.today().year + 1)],
    ["--since-year", "1999"],
    ["--since-year", "0"],
    ["--since-year", str(date.today().year + 1)],
    ["--locked-before", "2025-13-01"],
    ["--locked-before", "abc"],
])
def test_ac9_invalid_args_exit2_before_connect(argv, patch_base, capsys):
    """AC-9 / INV-AUDIT-04: неверные параметры -> 2 до подключения к базе."""
    patch_base(_Boom())
    assert main(["--base", "x", *argv]) == 2
    cap = capsys.readouterr()
    assert "Traceback" not in cap.err + cap.out


def test_ac9_boundary_today_is_allowed(fake_base, patch_base, monkeypatch, capsys):
    """AC-9 / INV-AUDIT-04: граница = сегодня не 'позже сегодняшнего дня'."""
    code, _, seen = _run(fake_base, patch_base, monkeypatch, capsys,
                         ["--locked-before", date.today().isoformat()])
    assert code != 2
    assert seen == [date.today()]


@pytest.mark.parametrize("year", [2000, date.today().year])
def test_ac9_since_year_bounds_allowed(year, fake_base, patch_base, monkeypatch, capsys):
    """AC-9 / INV-AUDIT-04: since_year 2000 и текущий год допустимы."""
    code, _, _ = _run(fake_base, patch_base, monkeypatch, capsys, ["--since-year", str(year)])
    assert code != 2


# ---------- AC-10 ----------
@pytest.mark.parametrize("tz", [timezone.utc, timezone(timedelta(hours=3)),
                                timezone(timedelta(hours=-5))])
def test_ac10_tz_aware_same_boundary(tz, fake_base):
    """AC-10 / INV-AUDIT-80: datetime с tzinfo дает ту же границу, что без пояса."""
    _setup(fake_base, reg_date=datetime(2024, 12, 31, tzinfo=tz))
    opt = Options()
    resolve_boundary(fake_base, Report(echo=False), opt)
    assert opt.locked_before == date(2025, 1, 1)


# ---------- У2: opt.locked_source ----------
@pytest.mark.parametrize("kw,src", [
    ({}, "дата запрета в базе"),
    (dict(const_value=False), "не задана"),
    (dict(reg_date=datetime(1, 1, 1, tzinfo=timezone.utc)), "не задана"),
])
def test_u2_resolve_sets_locked_source(kw, src, fake_base):
    """AC-2/AC-3/AC-5 / INV-AUDIT-04: resolve_boundary пишет источник в opt.locked_source."""
    _setup(fake_base, **kw)
    opt = Options()
    resolve_boundary(fake_base, Report(echo=False), opt)
    assert opt.locked_source == src


def test_u2_main_param_sets_locked_source(fake_base, patch_base, monkeypatch, capsys):
    """AC-1/AC-5 / INV-AUDIT-04: main при параметре ставит opt.locked_source='параметр'."""
    got = []

    def spy(b, r, opt):
        got.append(opt.locked_source)

    _only_checks(monkeypatch, [spy])
    patch_base(fake_base)
    main(["--base", "x", "--locked-before", "2025"])
    assert got == ["параметр"]


# ---------- С1: перечисление по метаданным ----------
@pytest.mark.parametrize("enum_values", [None, ["ДляВсехИнформационныхБаз"]],
                         ids=["no_enum", "no_value"])
def test_c1_missing_enum_or_value_means_no_boundary(enum_values, fake_base):
    """AC-3 / INV-AUDIT-14, INV-AUDIT-03: нет перечисления или значения -> 'не задана', отказа нет."""
    _setup(fake_base, enum=False)
    if enum_values is not None:
        fake_base.enums["ВидыНазначенияДатЗапрета"] = enum_values
    opt = Options()
    r = Report(echo=False)
    resolve_boundary(fake_base, r, opt)
    assert opt.locked_before is None
    assert opt.locked_source == "не задана"
    assert len(r.failures) == 0
