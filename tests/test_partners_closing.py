"""Дубли контрагентов по паре ИНН+КПП; остатки закрытия месяца.
Спека: docs/dev/2026-09-30-spec-partners-closing.md
Инварианты: INV-AUDIT-10, 50, 51, 60, 81, 82.
Тесты написаны по спеке, без чтения реализации.
"""
from __future__ import annotations

import re
import types
from datetime import date

import pytest

from onec_audit import checks as checks_mod
from onec_audit.checks import Options
from onec_audit.report import Report

M_DUP = "// контрагенты: один ИНН"
M_NOINN = "// список: без ИНН"
M_BAL = "// закрытие: остатки"
M_OPS = "// закрытие: операции"


def ns(**kw):
    return types.SimpleNamespace(**kw)


def _m(marker):
    return lambda text, params: text.lstrip().startswith(marker)


def _run(check, fake, capsys, **opt_kw):
    r = Report(echo=True)
    capsys.readouterr()
    check(fake, r, Options(**opt_kw))
    body = capsys.readouterr().out
    r.summary()
    return body, capsys.readouterr().out, r


def _el(inn, kpp, code, name):
    return ns(ИНН=inn, КПП=kpp, Код=code, Наим=name)


def _dupe_base(fake, rows):
    fake.on(_m(M_NOINN), [])
    fake.on(_m(M_DUP), rows)
    return fake


def _dupe_line(summ):
    return [ln for ln in summ.splitlines() if "дубли контрагентов" in ln]


# ---------- Корень 1: partners_dupes ----------
@pytest.mark.partners_dupes
def test_ac1_dupe_pair_plus_branch(fake_base, capsys):
    """AC-1 / INV-AUDIT-50, 51: X/A x2 + X/B x1 -> дубль X/A в сводке, B в теле как филиал."""
    _dupe_base(fake_base, [
        _el("7700000001", "770001001", "00A1", "Альфа-1"),
        _el("7700000001", "770001001", "00A2", "Альфа-2"),
        _el("7700000001", "770002002", "00B1", "Альфа-филиал"),
    ])
    body, summ, _ = _run(checks_mod.partners, fake_base, capsys)
    lines = _dupe_line(summ)
    assert len(lines) == 1
    ln = lines[0]
    assert "дубли контрагентов: 1 групп" in ln
    assert "ИНН 7700000001 КПП 770001001" in ln
    for tok in ("00A1", "Альфа-1", "00A2", "Альфа-2"):
        assert tok in ln
    assert "00B1" not in summ and "770002002" not in summ
    assert "Не дубли - филиалы (один ИНН, разные КПП)" in body  # У3
    assert "КПП 770002002" in body and "00B1" in body and "Альфа-филиал" in body
    assert ", " in ln  # У1: элементы группы через ', '


@pytest.mark.partners_dupes
def test_ac2_only_branches_no_dupe(fake_base, capsys):
    """AC-2 / INV-AUDIT-50: Y: КПП A, B, C по одному -> сводка молчит, филиалы в теле."""
    _dupe_base(fake_base, [
        _el("7700000002", "770001001", "00Y1", "Игрек-1"),
        _el("7700000002", "770002002", "00Y2", "Игрек-2"),
        _el("7700000002", "770003003", "00Y3", "Игрек-3"),
    ])
    body, summ, r = _run(checks_mod.partners, fake_base, capsys)
    assert "дубли контрагентов" not in summ
    for code in ("00Y1", "00Y2", "00Y3"):
        assert code in body
    assert len(r.failures) == 0


@pytest.mark.partners_dupes
def test_ac3_triple_one_dupe(fake_base, capsys):
    """AC-3 / INV-AUDIT-50, 51: Z/A x3 -> один дубль с тремя кодами."""
    _dupe_base(fake_base, [
        _el("7700000003", "770001001", "00Z1", "Зет-1"),
        _el("7700000003", "770001001", "00Z2", "Зет-2"),
        _el("7700000003", "770001001", "00Z3", "Зет-3"),
    ])
    _, summ, _ = _run(checks_mod.partners, fake_base, capsys)
    lines = _dupe_line(summ)
    assert len(lines) == 1
    assert "дубли контрагентов: 1 групп" in lines[0]
    for code in ("00Z1", "00Z2", "00Z3"):
        assert code in lines[0]


@pytest.mark.partners_dupes
def test_two_dupe_groups_counted_separately(fake_base, capsys):
    """INV-AUDIT-50: две подгруппы одного ИНН с 2+ элементами -> 2 групп, разделитель '; '."""
    _dupe_base(fake_base, [
        _el("7700000004", "1", "0D1", "Д1"), _el("7700000004", "1", "0D2", "Д2"),
        _el("7700000004", "2", "0D3", "Д3"), _el("7700000004", "2", "0D4", "Д4"),
    ])
    _, summ, _ = _run(checks_mod.partners, fake_base, capsys)
    ln = _dupe_line(summ)[0]
    assert "дубли контрагентов: 2 групп" in ln
    assert "КПП 1:" in ln and "КПП 2:" in ln and "; " in ln  # У1


@pytest.mark.partners_dupes
def test_empty_inn_not_dupe(fake_base, capsys):
    """INV-AUDIT-50: пустой ИНН дублем не считается, даже при одинаковом КПП."""
    _dupe_base(fake_base, [
        _el("", "770001001", "00E1", "Пусто-1"),
        _el("", "770001001", "00E2", "Пусто-2"),
    ])
    _, summ, _ = _run(checks_mod.partners, fake_base, capsys)
    assert "дубли контрагентов" not in summ


@pytest.mark.partners_dupes
def test_ac4_dupe_query_fails_partners_fails_noinn_printed(fake_base, capsys):
    """AC-4 / INV-AUDIT-10, 51: отказ запроса дублей -> отказ partners, список без ИНН печатается."""
    fake_base.on(_m(M_NOINN), [ns(Код="000009", Наим="ООО Без-ИНН", Физ=False, ЕстьДокументы=True)])
    fake_base.rules.insert(0, (_m(M_DUP), RuntimeError("boom")))
    body, summ, r = _run(checks_mod.partners, fake_base, capsys)
    assert len(r.failures) == 1 and "partners" in str(r.failures)
    assert "000009" in body and "ООО Без-ИНН" in body
    assert "дубли контрагентов" not in summ


# ---------- Корень 2: closing_balances ----------
def _closing_queries(fake_base, capsys, year, month, rows=()):
    fake_base.names_by_kind["Документы"] = ["РегламентнаяОперация"]  # INV-AUDIT-14
    fake_base.on(_m(M_BAL), list(rows))
    _run(checks_mod.month_closing, fake_base, capsys,
         locked_before=date(year, month, 1))
    return [q for q in fake_base.queries if q.lstrip().startswith(M_BAL)]


def _month_only(monkeypatch, ym):
    monkeypatch.setattr(checks_mod, "months_to_check", lambda opt, today: [ym])


@pytest.mark.closing_balances
@pytest.mark.parametrize("ym,literal", [
    ((2025, 3), "ДАТАВРЕМЯ(2025, 4, 1"),
    ((2025, 12), "ДАТАВРЕМЯ(2026, 1, 1"),
])
def test_ac5_next_month_start_literal(ym, literal, fake_base, capsys, monkeypatch):
    """AC-5 / INV-AUDIT-82: остаток на начало следующего месяца, без 23, 59, 59."""
    _month_only(monkeypatch, ym)
    qs = _closing_queries(fake_base, capsys, *ym)
    assert qs, "запрос остатков с маркером не выполнялся"
    for q in qs:
        assert literal in q
        assert "23, 59, 59" not in q
        assert "90.09" not in q  # AC-10: перечень 20, 25, 26, 44


@pytest.mark.closing_balances
def test_ac6_one_row_per_account_one_fragment(fake_base, capsys, monkeypatch):
    """AC-6 / INV-AUDIT-82: запрос отдал одну строку по 20 -> ровно один фрагмент '20 <сумма>'."""
    _month_only(monkeypatch, (2025, 3))
    fake_base.names_by_kind["Документы"] = ["РегламентнаяОперация"]  # INV-AUDIT-14
    fake_base.on(_m(M_BAL), [ns(Код="20", Сумма=1234.56)])
    body, _, _ = _run(checks_mod.month_closing, fake_base, capsys,
                      locked_before=date(2025, 3, 1))
    # У4: фрагменты в квадратных скобках через '; ', сумма в формате money()
    assert len(re.findall(r"\[20 [^\];]*1\D?234,56[^\];]*\]", body)) == 1


@pytest.mark.closing_balances
def test_ac7_group_by_account_not_account_code(fake_base, capsys, monkeypatch):
    """AC-7 / INV-AUDIT-60: есть СГРУППИРОВАТЬ ПО, нет СГРУППИРОВАТЬ ПО Ост.Счет.Код."""
    _month_only(monkeypatch, (2025, 3))
    qs = _closing_queries(fake_base, capsys, 2025, 3)
    assert qs
    for q in qs:
        assert "СГРУППИРОВАТЬ ПО" in q
        assert "СГРУППИРОВАТЬ ПО Ост.Счет.Код" not in q
        assert "90.09" not in q  # AC-10
        assert "СГРУППИРОВАТЬ ПО Ост.Счет" in q  # уточнение INV-AUDIT-60


# ---------- Корень 3: closing_state ----------
def _months(monkeypatch, yms):
    monkeypatch.setattr(checks_mod, "months_to_check", lambda opt, today: list(yms))


def _state_base(fake, ops=(), bal=()):
    fake.names_by_kind["Документы"] = ["РегламентнаяОперация"]  # INV-AUDIT-14
    fake.on(_m(M_OPS), list(ops))
    fake.on(_m(M_BAL), list(bal))
    return fake


def _month_line(body, ym):
    return next(ln for ln in body.splitlines() if ym in ln)


@pytest.mark.closing_state
def test_ac8_ops_without_postings_is_closed(fake_base, capsys, monkeypatch):
    """AC-8 / INV-AUDIT-81: Всего=8, СДвижениями=0 -> 'закрыт', не в сводке, остатки не печатаются."""
    _months(monkeypatch, [(2025, 12)])
    _state_base(fake_base, ops=[ns(Год=2025, Месяц=12, Всего=8, СДвижениями=0)],
                bal=[ns(Код="20", Сумма=500.0)])
    body, summ, r = _run(checks_mod.month_closing, fake_base, capsys)
    ln = _month_line(body, "2025-12")
    assert "операций 8, с проводками 0" in ln and "закрыт" in ln
    assert "не закрывался" not in ln and "[" not in ln
    assert "не закрывался месяц" not in summ
    assert len(r.failures) == 0


@pytest.mark.closing_state
def test_ac9_no_row_is_not_closed_and_in_summary(fake_base, capsys, monkeypatch):
    """AC-9 / INV-AUDIT-81: нет строки в ответе -> 'не закрывался', в сводке."""
    _months(monkeypatch, [(2026, 2)])
    _state_base(fake_base)
    body, summ, _ = _run(checks_mod.month_closing, fake_base, capsys)
    assert "не закрывался" in _month_line(body, "2026-02")
    assert "не закрывался месяц: 1 (2026-02 .. 2026-02)" in summ


@pytest.mark.closing_state
def test_ac10_no_9009_and_balances_only_for_unclosed(fake_base, capsys, monkeypatch):
    """AC-10 / INV-AUDIT-82: запрос остатков без 90.09; остатки только у 'не закрывался'."""
    _months(monkeypatch, [(2025, 11), (2025, 12)])
    _state_base(fake_base, ops=[ns(Год=2025, Месяц=11, Всего=3, СДвижениями=2)],
                bal=[ns(Код="26", Сумма=1234.56)])
    body, _, _ = _run(checks_mod.month_closing, fake_base, capsys)
    qs = [q for q in fake_base.queries if q.lstrip().startswith(M_BAL)]
    assert qs, "остатки не запрашивались для месяца 'не закрывался'"
    assert all("90.09" not in q for q in qs)
    assert "[" not in _month_line(body, "2025-11")
    assert "[26 " in _month_line(body, "2025-12")


@pytest.mark.closing_state
def test_ac11_five_months_one_summary_line(fake_base, capsys, monkeypatch):
    """AC-11 / INV-AUDIT-81: пять месяцев подряд 'не закрывался' -> одна строка сводки с диапазоном."""
    _months(monkeypatch, [(2026, m) for m in range(2, 7)])
    _state_base(fake_base)
    _, summ, _ = _run(checks_mod.month_closing, fake_base, capsys)
    lines = [ln for ln in summ.splitlines() if "не закрывался месяц" in ln]
    assert len(lines) == 1
    assert "не закрывался месяц: 5 (2026-02 .. 2026-06)" in lines[0]


@pytest.mark.closing_state
def test_all_closed_no_summary_line(fake_base, capsys, monkeypatch):
    """AC-8 / INV-AUDIT-81: все месяцы закрыты -> строки 'не закрывался месяц' нет."""
    _months(monkeypatch, [(2025, 1), (2025, 2)])
    _state_base(fake_base, ops=[ns(Год=2025, Месяц=1, Всего=2, СДвижениями=1),
                                ns(Год=2025, Месяц=2, Всего=1, СДвижениями=1)])
    _, summ, _ = _run(checks_mod.month_closing, fake_base, capsys)
    assert "не закрывался месяц" not in summ


@pytest.mark.closing_state
def test_old_states_absent(fake_base, capsys, monkeypatch):
    """AC-8 / INV-AUDIT-81: состояний 'не закрыт' и 'частично' по флагу больше нет."""
    _months(monkeypatch, [(2025, 12)])
    _state_base(fake_base, ops=[ns(Год=2025, Месяц=12, Всего=8, СДвижениями=5, Проведено=0)])
    body, summ, _ = _run(checks_mod.month_closing, fake_base, capsys)
    assert "частично" not in body + summ
    assert not re.search(r"не закрыт(?!ся)", body + summ)
