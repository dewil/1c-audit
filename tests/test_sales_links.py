"""Адресные списки. Спека: docs/dev/2026-09-30-spec-sales-links.md
Инварианты: INV-AUDIT-05, 09, 20..25, 30..32, 52.
Тесты написаны по спеке, без чтения реализации.
"""
from __future__ import annotations

import re
import types
from datetime import date, datetime

import pytest

from onec_audit import checks as checks_mod
from onec_audit.checks import Options
from onec_audit.report import Report

pytestmark = pytest.mark.sales_links

OPEN = datetime(2025, 3, 10)
OLD = datetime(2024, 5, 7)
BOUNDARY = date(2025, 1, 1)
REAL, INV, PAY = ("Документ.РеализацияТоваровУслуг", "Документ.СчетНаОплатуПокупателю",
                  "Документ.ПоступлениеНаРасчетныйСчет")
MONEY = re.compile(r"\d[\d\s]*[.,]\d{2}\b")


def ns(**kw):
    return types.SimpleNamespace(**kw)


def _m(marker):
    """Маркер запроса (У1)."""
    return lambda text, params: text.lstrip().startswith(marker)


M_ACT, M_INV, M_PAY = ("// список: акты без счёта", "// список: счета без акта и оплаты",
                       "// список: поступления без счёта")
_MARK = {REAL: M_ACT, INV: M_INV, PAY: M_PAY}


def _from(table):
    return _m(_MARK[table])


def _link_row(num="0001", d=OPEN, contr="Ромашка", inn="7701234567", **extra):
    return ns(Дата=d, Номер=num, Контр=contr, ИНН=inn, **extra)


def _setup_links(fake, acts=(), invoices=(), pays=()):
    fake.on(_from(PAY), list(pays))
    fake.on(_from(INV), list(invoices))
    fake.on(_from(REAL), list(acts))
    return fake


def _run(check, fake, capsys, locked=None):
    opt = Options(locked_before=locked)
    r = Report(echo=True)
    capsys.readouterr()
    check(fake, r, opt)
    body = capsys.readouterr().out
    r.summary()
    return body, capsys.readouterr().out, r


def _links():
    from onec_audit.checks import document_links
    return document_links


def _unposted():
    return checks_mod.unposted_documents


def _partners():
    return checks_mod.partners


# ---------- AC-1 / AC-2: контрагенты без ИНН ----------
def _no_inn_base(fake, rows):
    fake.on(_m("// список: без ИНН"), rows)
    return fake


def test_ac1_no_inn_list_and_summary(fake_base, capsys):
    """AC-1 / INV-AUDIT-05, 20: три строки в теле; в сводке только юрлицо с документами."""
    _no_inn_base(fake_base, [
        ns(Код="000001", Наим="ООО Альфа", Физ=False, ЕстьДокументы=True),
        ns(Код="000002", Наим="ООО Бета", Физ=False, ЕстьДокументы=False),
        ns(Код="000003", Наим="Иванов И.", Физ=True, ЕстьДокументы=True),
    ])
    body, summ, _ = _run(_partners(), fake_base, capsys)
    for code, name in [("000001", "ООО Альфа"), ("000002", "ООО Бета"), ("000003", "Иванов И.")]:
        assert code in body and name in body
    assert "юрлиц без ИНН с документами: 1 (ООО Альфа)" in summ
    assert "ООО Бета" not in summ and "Иванов" not in summ


def test_ac2_no_inn_empty(fake_base, capsys):
    """AC-2 / INV-AUDIT-20: пусто -> в теле 'без ИНН: 0', в сводке строки нет."""
    _no_inn_base(fake_base, [])
    body, summ, _ = _run(_partners(), fake_base, capsys)
    assert "без ИНН: 0" in body
    assert "юрлиц без ИНН" not in summ


# ---------- AC-3..AC-5: непроведенные ----------
def _unposted_base(fake, typ, rows, closed=0):
    """У2: счетчики - one() (Всего, Непров, Открыт), список открытых - rows() с маркером."""
    fake.names_by_kind["Документы"] = [typ]
    marker = f"// непроведённые: {typ}"
    fake.on(_m(marker), rows)
    cnt = ns(Всего=len(rows) + closed + 5, Непров=len(rows) + closed, Открыт=len(rows))
    fake.on(lambda t, p: f"Документ.{typ}" in t and not t.lstrip().startswith("//"), cnt)
    return fake


def test_ac3_service_types_constant():
    """AC-3 / INV-AUDIT-21: перечень служебных - frozenset, РегламентнаяОперация в нем нет."""
    from onec_audit.checks import SERVICE_DOC_TYPES
    assert isinstance(SERVICE_DOC_TYPES, frozenset)
    assert {"ПакетОбменСБанками", "СообщениеОбменСБанками"} <= SERVICE_DOC_TYPES
    assert "РегламентнаяОперация" not in SERVICE_DOC_TYPES


def test_ac3_service_type_not_in_summary(fake_base, capsys):
    """AC-3 / INV-AUDIT-21: служебный тип печатается в теле, в сводку не идет."""
    _unposted_base(fake_base, "ПакетОбменСБанками", [ns(Дата=OPEN, Номер="П-1")])
    body, summ, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "ПакетОбменСБанками" in body
    assert "непроведённых в открытом периоде" not in summ


def test_ac3_regulatory_not_in_summary_counter(fake_base, capsys):
    """AC-3 / INV-AUDIT-21: РегламентнаяОперация не входит в счетчик сводки."""
    _unposted_base(fake_base, "РегламентнаяОперация", [ns(Дата=OPEN, Номер="Р-1")])
    _, summ, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "непроведённых в открытом периоде" not in summ


def test_ac4_arbitrary_type_list_and_summary(fake_base, capsys):
    """AC-4 / INV-AUDIT-22: список дата, номер, контрагент; сводка без 'всего'."""
    _unposted_base(fake_base, "ПоступлениеТоваровУслуг", [
        ns(Дата=OPEN, Номер="0042", Контр="ООО Гамма"),
        ns(Дата=datetime(2025, 4, 1), Номер="0043", Контр="ООО Дельта"),
    ])
    body, summ, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "2025-03-10" in body and "0042" in body and "ООО Гамма" in body
    assert "непроведённых в открытом периоде: 2" in summ
    assert "всего" not in summ


def test_ac4_type_without_partner_dash(fake_base, capsys):
    """AC-4 / INV-AUDIT-22: у типа нет реквизита Контрагент -> прочерк."""
    _unposted_base(fake_base, "АвансовыйОтчет", [ns(Дата=OPEN, Номер="А-7")])
    body, _, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    line = next(ln for ln in body.splitlines() if "А-7" in ln)
    assert line.split("А-7", 1)[1].strip().endswith("-")


def test_ac5_only_closed_summary_silent(fake_base, capsys):
    """AC-5 / INV-AUDIT-22, 23: только закрытые -> сводка молчит, в теле число закрытых (У4)."""
    _unposted_base(fake_base, "ПоступлениеТоваровУслуг", [], closed=2)
    body, summ, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "непроведённых в открытом периоде" not in summ
    assert "   ПоступлениеТоваровУслуг: в закрытом периоде 2" in body


# ---------- AC-6..AC-8, AC-10, AC-11: связи ----------
def test_ac6_three_lists(fake_base, capsys):
    """AC-6 / INV-AUDIT-24, 25: три списка, ИНН, вид операции, без сумм, три строки сводки."""
    _setup_links(fake_base,
                 acts=[_link_row("А-1", contr="Акт-Контр", inn="1111111111")],
                 invoices=[_link_row("С-1", contr="Счет-Контр", inn="2222222222")],
                 pays=[_link_row("П-1", contr="Пл-Контр", inn="3333333333", Вид="Эквайринг")])
    body, summ, _ = _run(_links(), fake_base, capsys, BOUNDARY)
    assert "7." in body
    for tok in ["А-1", "Акт-Контр", "1111111111", "С-1", "Счет-Контр", "2222222222",
                "П-1", "Пл-Контр", "3333333333", "Эквайринг", "2025-03-10"]:
        assert tok in body, tok
    assert not MONEY.search(body)
    assert "акты без счёта: 1" in summ
    assert "счета без акта и оплаты: 1" in summ
    assert "поступления без счёта: 1" in summ


def test_ac7_closed_marked_and_not_in_summary(fake_base, capsys):
    """AC-7 / INV-AUDIT-30: раньше границы -> [закрыт] в конце строки, в сводку не идет."""
    _setup_links(fake_base,
                 acts=[_link_row("А-old", d=OLD)],
                 invoices=[_link_row("С-old", d=OLD)],
                 pays=[_link_row("П-old", d=OLD, Вид="Прочее")])
    body, summ, _ = _run(_links(), fake_base, capsys, BOUNDARY)
    for num in ("А-old", "С-old", "П-old"):
        line = next(ln for ln in body.splitlines() if num in ln)
        assert line.rstrip().endswith("[закрыт]")
    assert "без счёта" not in summ and "без акта и оплаты" not in summ


def test_ac7_open_not_marked(fake_base, capsys):
    """AC-7 / INV-AUDIT-30: открытая строка без пометки."""
    _setup_links(fake_base, acts=[_link_row("А-new")])
    body, _, _ = _run(_links(), fake_base, capsys, BOUNDARY)
    line = next(ln for ln in body.splitlines() if "А-new" in ln)
    assert "[закрыт]" not in line


def test_ac8_all_empty(fake_base, capsys):
    """AC-8 / INV-AUDIT-31: все списки пусты -> '0' в теле, сводка молчит."""
    _setup_links(fake_base)
    body, summ, r = _run(_links(), fake_base, capsys, BOUNDARY)
    assert "7." in body
    for name in ("Акты без счёта", "Счета без акта и оплаты", "Поступления без счёта"):
        assert f"   {name}: 0" in body
    assert "без счёта" not in summ and "без акта и оплаты" not in summ
    assert len(r.failures) == 0


@pytest.mark.parametrize("which", [REAL, INV, PAY])
def test_ac10_one_list_fails_others_print(which, fake_base, capsys):
    """AC-10 / INV-AUDIT-09, 32: отказ одного запроса -> отказ document_links, остальные печатаются."""
    fake = _setup_links(fake_base, [_link_row("А-1")], [_link_row("С-1")],
                        [_link_row("П-1", Вид="В")])
    fake.rules.insert(0, (_from(which), RuntimeError("boom")))
    body, _, r = _run(_links(), fake, capsys, BOUNDARY)
    assert len(r.failures) == 1 and "document_links" in str(r.failures)
    for t, num in [(REAL, "А-1"), (INV, "С-1"), (PAY, "П-1")]:
        assert (num in body) == (t != which)


def test_document_links_registered_after_month_closing():
    """Контракт / INV-AUDIT-52: document_links в ALL_CHECKS сразу после month_closing."""
    names = [f.__name__ for f in checks_mod.ALL_CHECKS]
    assert names.index("document_links") == names.index("month_closing") + 1


def test_ac11_unposted_realization_one_summary_line(fake_base, capsys):
    """AC-11 / INV-AUDIT-52: непроведенная реализация дает одну строку сводки ('непроведённых')."""
    _unposted_base(fake_base, "РеализацияТоваровУслуг",
                   [ns(Дата=OPEN, Номер="Р-9", Контр="ООО Эпсилон", ЕстьСчет=False, Счет=None)])
    fake_base.on(_m("// оплата счёта"), None)
    _, summ, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    lines = [ln for ln in summ.splitlines() if "непроведённ" in ln or "реализац" in ln.lower()]
    assert sum("непроведённых в открытом периоде: 1" in ln for ln in lines) == 1
    assert len(lines) == 1


# ---------- AC-9: признак оплаты ----------
def _sales_unposted(fake, typ, row, pay):
    _unposted_base(fake, typ, [row])
    fake.rules.insert(0, (_m("// оплата счёта"), pay))
    return fake


def _line(body, num):
    return next(ln for ln in body.splitlines() if num in ln)


def test_ac9_paid_yes_with_date(fake_base, capsys):
    """AC-9 / INV-AUDIT-31: на непроведенный счет ссылается поступление -> 'ДА <дата>'."""
    _sales_unposted(fake_base, "СчетНаОплатуПокупателю",
                    ns(Дата=OPEN, Номер="С-5", Контр="К", Ссылка="ref-5"),
                    ns(Дата=datetime(2025, 3, 20)))
    body, _, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "ДА" in _line(body, "С-5") and "2025-03-20" in _line(body, "С-5")


def test_ac9_paid_no(fake_base, capsys):
    """AC-9 / INV-AUDIT-31: нет ссылки -> 'НЕТ'."""
    _sales_unposted(fake_base, "СчетНаОплатуПокупателю",
                    ns(Дата=OPEN, Номер="С-6", Контр="К", Ссылка="ref-6"), None)
    body, _, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "НЕТ" in _line(body, "С-6")


def test_ac9_realization_without_invoice(fake_base, capsys):
    """AC-9 / INV-AUDIT-31: непроведенная реализация без счета в шапке -> 'нет счёта'."""
    _sales_unposted(fake_base, "РеализацияТоваровУслуг",
                    ns(Дата=OPEN, Номер="Р-3", Контр="К", ЕстьСчет=False, Счет=None), None)
    body, _, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "нет счёта" in _line(body, "Р-3")


def test_ac9_sum_not_passed_to_payment_query(fake_base, capsys):
    """AC-9 / INV-AUDIT-31: в запрос оплаты уходит только Счет, сумма не передается (У3)."""
    calls = []

    def spy(t, p):
        if t.lstrip().startswith("// оплата счёта"):
            calls.append(p)
        return False

    _sales_unposted(fake_base, "СчетНаОплатуПокупателю",
                    ns(Дата=OPEN, Номер="С-7", Контр="К", Ссылка="ref-7", Сумма=777.77), None)
    fake_base.rules.insert(0, (spy, None))
    _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert calls
    for p in calls:
        assert set(p) == {"Счет"}
