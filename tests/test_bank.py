"""Обмен с банком по ссылкам. Спека: docs/dev/2026-09-30-spec-bank.md
Инварианты: INV-AUDIT-03, 05, 07, 40, 41, 43, 44.
"""
from __future__ import annotations

import types
from datetime import date, datetime
from decimal import Decimal

import pytest

from onec_audit.checks import Options
from onec_audit.report import Report

pytestmark = pytest.mark.bank

MSG = "// банк: сообщения"
BY_REQ = "// банк: списание по реквизитам"
BANK_CLEAN = "незавершённых сообщений, требующих внимания, нет"
DISCLAIMER = "статус сообщения сам по себе не доказывает"
SUMMARY_PREFIX = "банк: платёжки без списания"
ORPHAN_NOTE = "платежка удалена, списания не найдено"


def bank_exchange(b, r, opt):
    from onec_audit.checks import bank_exchange as f  # импорт внутри
    return f(b, r, opt)


def _row(**kw):
    d = dict(Дата=datetime(2025, 12, 30), Статус="НеПодтвержден", ЭтоПП=True,
             Номер="0001", Контр="ООО Ромашка", ИНН="7701000001",
             Сумма=Decimal("1000.00"), Платежка=object(), ПлатежкаНомер="ПП-77",
             Списано=False)
    d.update(kw)
    return types.SimpleNamespace(**d)


def _orphan(**kw):
    return _row(Платежка=None, ПлатежкаНомер=None, **kw)


def _run(fake, rows, capsys, *, by_req=None, opt=None):
    fake.fill_names = True
    fake.on(BY_REQ, by_req)  # первым: маркер реквизитного запроса
    fake.on(MSG, rows)
    r = Report(echo=True)
    capsys.readouterr()
    bank_exchange(fake, r, opt or Options())
    body = capsys.readouterr().out
    r.summary()
    return body, capsys.readouterr().out, r


def test_ac1_paid_in_body_not_in_summary(fake_base, capsys):
    """AC-1 / INV-AUDIT-05, 40: связана, списание есть -> 'деньги ушли' в теле, не в сводке."""
    body, summ, r = _run(fake_base, [_row(Списано=True)], capsys)
    assert "деньги ушли" in body
    assert "ПП-77" in body
    assert SUMMARY_PREFIX not in summ
    assert r.exit_code() == 0


def test_ac2_no_payment_in_body_and_summary(fake_base, capsys):
    """AC-2 / INV-AUDIT-05, 41: связана, списания нет -> тело и сводка: дата, номер платежки, получатель."""
    body, summ, r = _run(fake_base, [_row()], capsys)
    for text in (body, summ):
        assert "ПП-77" in text and "ООО Ромашка" in text
        assert "2025-12-30" in text
    assert SUMMARY_PREFIX in summ
    assert r.exit_code() == 1


def test_ac3_orphan_with_payment_found_listed_not_in_summary(fake_base, capsys):
    """AC-3 / INV-AUDIT-05, 43: сирота, списание по реквизитам найдено -> список сирот, не в сводке."""
    body, summ, r = _run(fake_base, [_orphan(Номер="СИР-9")], capsys,
                         by_req=types.SimpleNamespace(Дата=datetime(2026, 1, 9)))
    assert "платежку удалили и пересоздали, деньги ушли по новому документу" in body
    assert "СИР-9" in body
    assert SUMMARY_PREFIX not in summ
    assert ORPHAN_NOTE not in body + summ
    assert r.exit_code() == 0


def test_ac4_orphan_without_payment_in_summary(fake_base, capsys):
    """AC-4 / INV-AUDIT-05, 43: сирота, списания нет -> в сводке с пометкой."""
    body, summ, r = _run(fake_base, [_orphan(Номер="СИР-9")], capsys, by_req=None)
    assert ORPHAN_NOTE in summ
    assert SUMMARY_PREFIX in summ
    assert "ООО Ромашка" in summ
    assert "2025-12-30" in summ
    assert r.exit_code() == 1


def test_orphan_lookup_params_inn_and_sum_not_float(fake_base, capsys):
    """AC-3 / INV-AUDIT-07, 44 (К-3): реквизитный запрос получает ИНН и Сумму как Decimal/строку, не float."""
    seen = []

    def spy(text, params):
        if BY_REQ in text:
            seen.append(params)
        return False
    fake_base.on(spy, None)
    _run(fake_base, [_orphan()], capsys)
    assert seen, "запрос списания по реквизитам не выполнен"
    assert seen[0]["ИНН"] == "7701000001"
    assert isinstance(seen[0]["Сумма"], (Decimal, str))
    assert not isinstance(seen[0]["Сумма"], float)


def test_linked_payment_does_not_query_by_requisites(fake_base, capsys):
    """AC-1/AC-2 / INV-AUDIT-05: реквизитный запрос нужен только сироте."""
    seen = []
    fake_base.on(lambda t, p: seen.append(t) if BY_REQ in t else False, None)
    _run(fake_base, [_row(), _row(Списано=True)], capsys)
    assert seen == []


def test_ac5_non_payment_message_body_only(fake_base, capsys):
    """AC-5 / INV-AUDIT-05: не ПП в незавершенном статусе -> в теле строкой со статусом и числом, не в сводке."""
    rows = [_orphan(ЭтоПП=False, Статус="Отправлен") for _ in range(3)]
    body, summ, r = _run(fake_base, rows, capsys)
    assert "Отправлен" in body and "3" in body
    assert SUMMARY_PREFIX not in summ
    assert ORPHAN_NOTE not in body + summ
    assert r.exit_code() == 0


def test_ac6_locked_marked_not_in_summary(fake_base, capsys):
    """AC-6 / INV-AUDIT-03: ПП без списания раньше границы -> [закрыт], не в сводке."""
    opt = Options(locked_before=date(2026, 1, 1))
    body, summ, r = _run(fake_base, [_row()], capsys, opt=opt)
    assert "[закрыт]" in body
    assert SUMMARY_PREFIX not in summ
    assert r.exit_code() == 0


def test_ac6_not_locked_after_boundary_has_no_mark(fake_base, capsys):
    """AC-6 (граница) / INV-AUDIT-03: сообщение не раньше границы -> без [закрыт], в сводке."""
    opt = Options(locked_before=date(2025, 6, 1))
    body, summ, _ = _run(fake_base, [_row()], capsys, opt=opt)
    assert "[закрыт]" not in body
    assert SUMMARY_PREFIX in summ


def test_ac6_locked_orphan_without_payment_not_in_summary(fake_base, capsys):
    """AC-6 / INV-AUDIT-03: сирота без списания раньше границы тоже [закрыт] и не в сводке."""
    opt = Options(locked_before=date(2026, 1, 1))
    body, summ, _ = _run(fake_base, [_orphan()], capsys, opt=opt)
    assert "[закрыт]" in body
    assert SUMMARY_PREFIX not in summ


@pytest.mark.parametrize("kind", ["paid", "attention", "orphan_found", "other"])
def test_ac7_fifteen_rows_printed_fully(fake_base, capsys, kind):
    """AC-7 / INV-AUDIT-07: 15 сообщений в группе -> все 15 строк в теле, без усечения."""
    by_req = None
    if kind == "paid":
        rows = [_row(Списано=True, ПлатежкаНомер=f"НОМ-{i:03d}") for i in range(15)]
    elif kind == "attention":
        rows = [_row(ПлатежкаНомер=f"НОМ-{i:03d}") for i in range(15)]
    elif kind == "orphan_found":
        rows = [_orphan(Номер=f"НОМ-{i:03d}") for i in range(15)]
        by_req = types.SimpleNamespace(Дата=datetime(2026, 1, 9))
    else:
        rows = [_row(ЭтоПП=False, Номер=f"НОМ-{i:03d}") for i in range(15)]
    body, _, _ = _run(fake_base, rows, capsys, by_req=by_req)
    if kind == "other":  # одной строкой на статус с числом
        assert "15" in body
    else:
        for i in range(15):
            assert f"НОМ-{i:03d}" in body, f"строка {i} усечена"


def test_ac7_attention_summary_lists_all_fifteen(fake_base, capsys):
    """AC-7 / INV-AUDIT-07, 41: в сводке адресно перечислены все 15 платежек, число N."""
    rows = [_row(ПлатежкаНомер=f"НОМ-{i:03d}") for i in range(15)]
    _, summ, _ = _run(fake_base, rows, capsys)
    assert f"{SUMMARY_PREFIX}: 15" in summ
    for i in range(15):
        assert f"НОМ-{i:03d}" in summ


def test_ac8_disclaimer_in_section(fake_base, capsys):
    """AC-8 / INV-AUDIT-40: оговорка про статус и признак списания печатается в секции."""
    body, _, _ = _run(fake_base, [_row(Списано=True)], capsys)
    assert DISCLAIMER in body
    assert "проведенное списание" in body or "проведённое списание" in body


def test_ac9_messages_query_failure_is_failure(fake_base, capsys):
    """AC-9 / INV-AUDIT-10: отказ запроса сообщений -> отказ, вердикта 'нет' нет."""
    fake_base.fill_names = True
    fake_base.raise_when(lambda t, p: MSG in t)
    r = Report(echo=True)
    bank_exchange(fake_base, r, Options())
    assert len(r.failures) == 1
    assert BANK_CLEAN not in capsys.readouterr().out
    assert r.exit_code() == 4


def test_ac9_requisites_query_failure_is_failure_and_attention(fake_base, capsys):
    """AC-9 / INV-AUDIT-10, У3: отказ реквизитного запроса -> отказ проверки, сирота в 'требует внимания'."""
    fake_base.fill_names = True
    fake_base.raise_when(lambda t, p: BY_REQ in t)
    fake_base.on(MSG, [_orphan(Номер="СИР-9")])
    r = Report(echo=True)
    capsys.readouterr()
    bank_exchange(fake_base, r, Options())
    out = capsys.readouterr().out
    assert len(r.failures) == 1
    assert BANK_CLEAN not in out
    assert "СИР-9" in out
    assert "платежку удалили и пересоздали" not in out


def test_ac10_no_incomplete_messages_clean_verdict(fake_base, capsys):
    """AC-10 / INV-AUDIT-05: нет ни одного незавершенного -> вердикт, сводка молчит."""
    body, summ, r = _run(fake_base, [], capsys)
    assert BANK_CLEAN in body
    assert SUMMARY_PREFIX not in summ
    assert r.exit_code() == 0


def test_no_clean_verdict_when_attention_present(fake_base, capsys):
    """AC-10 (отрицательное) / INV-AUDIT-05: при требующих внимания вердикта 'нет' нет."""
    body, _, _ = _run(fake_base, [_row()], capsys)
    assert BANK_CLEAN not in body


def test_number_year_mismatch_not_orphan(fake_base, capsys):
    """AC-2 / INV-AUDIT-05: связь из поля Платежка, не из номера/года: сообщение 09.01 к платежке 30.12 - не сирота."""
    row = _row(Дата=datetime(2026, 1, 9), Номер="999", ПлатежкаНомер="ПП-77")
    body, _, _ = _run(fake_base, [row], capsys)
    assert "платежка удалена" not in body
    assert "платежку удалили" not in body


def test_mixed_groups_summary_only_attention(fake_base, capsys):
    """AC-1/AC-2/AC-5 / INV-AUDIT-41: из смеси в сводку идет только 'требует внимания'."""
    rows = [
        _row(Списано=True, ПлатежкаНомер="ОК-1", Контр="Оплачено ООО"),
        _row(ПлатежкаНомер="ВН-2", Контр="Внимание ООО"),
        _row(ЭтоПП=False, Статус="Отправлен", Контр="Выписка ООО", ПлатежкаНомер="ВЫП-3"),
    ]
    _, summ, _ = _run(fake_base, rows, capsys)
    assert "ВН-2" in summ and "Внимание ООО" in summ
    assert "ОК-1" not in summ and "Оплачено ООО" not in summ
    assert "ВЫП-3" not in summ and "Выписка ООО" not in summ
    assert f"{SUMMARY_PREFIX}: 1" in summ
