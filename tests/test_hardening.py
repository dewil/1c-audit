"""Укрепление. Спека: docs/dev/2026-09-30-spec-hardening.md
Инварианты: INV-AUDIT-01, 02, 06, 12, 13, 14, 16, 17, 41, 70, 83, 90.
Тесты написаны по спеке, без чтения реализации.
AC-4 (К-18) проверяется прогоном на базе, здесь его нет.
"""
from __future__ import annotations

import ast
import io
import sys
import types
from datetime import date, datetime
from pathlib import Path

import pytest

from onec_audit import checks as checks_mod
from onec_audit.checks import Options
from onec_audit.report import Report

SRC = Path(__file__).resolve().parents[1] / "src" / "onec_audit"

M_DUP = "// контрагенты: один ИНН"
M_NOINN = "// список: без ИНН"
M_ACT = "// список: акты без счёта"
M_INV = "// список: счета без акта и оплаты"
M_PAY = "// список: поступления без счёта"
MSG = "// банк: сообщения"
BY_REQ = "// банк: списание по реквизитам"
OPEN = datetime(2025, 3, 10)
OLD = datetime(2024, 5, 7)
BOUNDARY = date(2025, 1, 1)


def ns(**kw):
    return types.SimpleNamespace(**kw)


def _m(marker):
    return lambda text, params: text.lstrip().startswith(marker)


# =====================================================================
# К-17. metadata_names
# =====================================================================
class _Finder:
    def __init__(self, items):
        self._items = items

    def Найти(self, name):  # noqa: N802
        return self._items.get(name)


class _MdItem:
    def __init__(self, attrs=(), tabs=None):
        self.Реквизиты = _Finder({a: ns(Имя=a) for a in attrs})
        self.ТабличныеЧасти = _Finder(
            {t: _MdItem(attrs=cols) for t, cols in (tabs or {}).items()})


class _MdColl(_Finder):
    pass


class _FakeMd:
    def __init__(self, **colls):
        self._colls = colls

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _MdColl(self._colls.get(name, {}))


def _md_base():
    md = _FakeMd(
        Документы={"X": _MdItem(attrs=["Рек"], tabs={"ТЧ": ["РекТЧ"]})},
        Справочники={"Контрагенты": _MdItem(attrs=["ИНН", "КПП"])},
        РегистрыБухгалтерии={"Хозрасчетный": _MdItem()},
    )
    return ns(md=md)


def _missing(paths):
    from onec_audit.checks import missing_metadata
    return missing_metadata(_md_base(), paths)


@pytest.mark.metadata_names
def test_ac1_all_present_empty():
    """AC-1 / INV-AUDIT-14: объект, реквизит шапки, реквизит ТЧ есть -> []."""
    assert _missing([
        "Документ.X", "Документ.X.Рек", "Документ.X.ТЧ.РекТЧ",
        "РегистрБухгалтерии.Хозрасчетный", "Справочник.Контрагенты.ИНН",
    ]) == []


@pytest.mark.metadata_names
def test_ac1_empty_paths_empty_result():
    """AC-1 / INV-AUDIT-14: пустой список путей -> []."""
    assert _missing([]) == []


@pytest.mark.metadata_names
def test_ac1_missing_object():
    """AC-1 / INV-AUDIT-14: нет объекта -> его путь."""
    assert _missing(["Документ.Нет"]) == ["Документ.Нет"]


@pytest.mark.metadata_names
def test_ac1_missing_header_attribute():
    """AC-1 / INV-AUDIT-14: нет реквизита шапки -> путь реквизита."""
    assert _missing(["Документ.X.НетРеквизита"]) == ["Документ.X.НетРеквизита"]


@pytest.mark.metadata_names
def test_ac1_missing_tabular_part():
    """AC-1 / INV-AUDIT-14: нет табличной части -> путь реквизита в ней."""
    assert _missing(["Документ.X.НетТЧ.РекТЧ"]) == ["Документ.X.НетТЧ.РекТЧ"]


@pytest.mark.metadata_names
def test_ac1_missing_tabular_attribute():
    """AC-1 / INV-AUDIT-14: нет реквизита табличной части -> путь реквизита."""
    assert _missing(["Документ.X.ТЧ.НетКолонки"]) == ["Документ.X.ТЧ.НетКолонки"]


@pytest.mark.metadata_names
def test_ac1_mixed_returns_only_missing():
    """AC-1 / INV-AUDIT-14: из смеси возвращаются только отсутствующие."""
    res = _missing(["Документ.X.Рек", "Документ.X.Нет", "Справочник.Контрагенты.ИНН",
                    "Справочник.Нет"])
    assert sorted(res) == ["Документ.X.Нет", "Справочник.Нет"]


@pytest.mark.metadata_names
def test_ac1_no_duplicates_first_appearance_order():
    """AC-1 / INV-AUDIT-14 (У1, У2): пути как переданы, без дублей, в порядке первого появления."""
    res = _missing(["Справочник.Б", "Документ.X.А", "Справочник.Б", "Документ.X.А", "Документ.В"])
    assert res == ["Справочник.Б", "Документ.X.А", "Документ.В"]


@pytest.mark.metadata_names
@pytest.mark.parametrize("std", ["Ссылка", "Дата", "Номер", "Проведен", "ПометкаУдаления"])
def test_ac2_standard_attribute_of_existing_object(std):
    """AC-2 / INV-AUDIT-14: стандартный реквизит документа существует, если есть объект."""
    assert _missing([f"Документ.X.{std}"]) == []


@pytest.mark.metadata_names
@pytest.mark.parametrize("std", ["Код", "Наименование", "ЭтоГруппа", "ПометкаУдаления"])
def test_ac2_standard_attribute_of_catalog(std):
    """AC-2 / INV-AUDIT-14: стандартный реквизит справочника существует, если есть объект."""
    assert _missing([f"Справочник.Контрагенты.{std}"]) == []


_CHECK_NAMES = ["partners", "document_links", "month_closing", "balances",
                "unposted_documents"]


def _ac3_base(fake):
    fake.names_by_kind["Документы"] = ["РеализацияТоваровУслуг", "РегламентнаяОперация"]
    fake.names_by_kind["Справочники"] = ["Контрагенты"]
    fake.on(_m("// непроведённые:"),
            [ns(Дата=OPEN, Номер="Р-9", Контр="К", ЕстьСчет=False, Счет=None)])
    fake.on(lambda t, p: "Документ." in t and not t.lstrip().startswith("//"),
            ns(Всего=10, Непров=2, Открыт=2))
    return fake


@pytest.mark.metadata_names
@pytest.mark.parametrize("name", _CHECK_NAMES)
def test_ac3_missing_metadata_registers_failure_no_queries(name, fake_base, monkeypatch, capsys):
    """AC-3 / INV-AUDIT-14: missing_metadata -> ['X.Y'] -> отказ с причиной, запросы не выполнялись."""
    import onec_audit.checks as c
    monkeypatch.setattr(c, "missing_metadata", lambda *a, **k: ["X.Y"])
    _ac3_base(fake_base)
    r = Report(echo=True)
    capsys.readouterr()
    getattr(c, name)(fake_base, r, Options(locked_before=BOUNDARY))
    out = capsys.readouterr().out
    r.summary()
    out += capsys.readouterr().out
    assert len(r.failures) == 1
    blob = str(r.failures) + out
    assert "в конфигурации нет" in blob and "X.Y" in blob
    if name == "unposted_documents":
        # детализация продаж: список реализаций и запрос оплаты не выполнялись
        assert not any(q.lstrip().startswith(("// непроведённые: РеализацияТоваровУслуг",
                                              "// оплата счёта")) for q in fake_base.queries)
    else:
        assert fake_base.queries == []


# =====================================================================
# К-19. mcp
# =====================================================================
class _Attrs(list):
    def Количество(self):  # noqa: N802
        return len(self)

    def Получить(self, i):  # noqa: N802
        return self[i]


class _Attr:
    def __init__(self, fill):
        self.Имя = "Реквизит"
        self.Синоним = "Реквизит"
        self._fill = fill

    @property
    def ПроверкаЗаполнения(self):  # noqa: N802
        return self._fill

    def __getattr__(self, name):
        return _Attrs()


_OK = ns(name="ВыдаватьОшибку")
_NO = ns(name="НеПроверять")
_THIRD = ns(name="СовсемДругоеЗначение")


@pytest.mark.mcp
@pytest.mark.parametrize("fill,expected", [(_OK, True), (_NO, False), (_THIRD, None)],
                         ids=["ВыдаватьОшибку", "НеПроверять", "третье"])
def test_ac5_describe_required_tristate(fill, expected, monkeypatch, fake_base):
    """AC-5 / INV-AUDIT-90: равно ВыдаватьОшибку -> true; НеПроверять -> false; иное -> null + note."""
    pytest.importorskip("mcp")
    from onec_audit import mcp_server as srv
    monkeypatch.setattr(srv, "_base", fake_base, raising=False)
    monkeypatch.setattr(srv, "_connect", lambda *a, **k: fake_base)
    obj = ns(Имя="Контрагенты", Синоним="К", Реквизиты=_Attrs([_Attr(fill)]),
             ТабличныеЧасти=_Attrs(), Измерения=_Attrs(), Ресурсы=_Attrs(), Макеты=_Attrs())
    fake_base.find = lambda coll, name: obj
    enum = ns(ВыдаватьОшибку=_OK, НеПроверять=_NO)
    fake_base.c = ns(ПроверкаЗаполнения=enum)
    fake_base.ПроверкаЗаполнения = enum
    res = srv._describe_sync("catalogs", "Контрагенты")
    assert res["attributes"], res
    item = res["attributes"][0]
    assert item["required"] is expected
    if expected is None:
        assert item["note"]


# =====================================================================
# Подменная база для полного прогона (AC-7, AC-9)
# =====================================================================
class _LooseRow:
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return 7


class _LooseList(list):
    """И список из одной строки, и объект с любыми полями: подходит и rows(), и one()."""

    def __init__(self):
        super().__init__([_LooseRow()])

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return 7


class _AnyFinder:
    def Найти(self, name):  # noqa: N802
        return _AnyItem()


class _AnyItem:
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _AnyFinder()


def _rich_base(seen_params=None):
    from conftest import FakeBase, _Coll, _Enums

    class AnyColl(_Coll):
        def Найти(self, name):  # noqa: N802
            return _AnyItem()

    class AnyMd:
        def __init__(self, enums):
            self._enums = _Enums(enums)

        @property
        def Перечисления(self):  # noqa: N802
            return self._enums

        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return AnyColl(name)

    class Rich(FakeBase):
        @property
        def md(self):
            return AnyMd(self.enums)

    from test_bank import _orphan, _row
    f = Rich()
    f.names_by_kind["Документы"] = [
        "ПлатежноеПоручение", "СписаниеСРасчетногоСчета", "СообщениеОбменСБанками",
        "РегламентнаяОперация", "ПакетОбменСБанками"]
    f.names_by_kind["РегистрыСведений"] = ["СвязанныеОбъектыОбменСБанками"]
    f.names_by_kind["Справочники"] = ["Контрагенты"]
    f.enums["ВидыЭДОбменСБанками"] = ["ПлатежноеПоручение"]
    if seen_params is not None:
        f.on(lambda t, p: (seen_params.append(dict(p)), False)[1], None)
    # помеченные на удаление: любой не-маркерный запрос с признаком
    f.on(lambda t, p: "ПометкаУдаления" in t and not t.lstrip().startswith("//"),
         _LooseList())
    f.on(lambda t, p: BY_REQ in t and p.get("ИНН") == "7701000001",
         ns(Дата=datetime(2026, 1, 9)))
    f.on(BY_REQ, None)
    f.on(MSG, [
        _orphan(Номер="СИР-9", ИНН="7701000001"),
        _row(ПлатежкаНомер="ВН-1", ИНН="7703000003", Контр="Внимание ООО"),
        _row(Дата=datetime(2024, 6, 1), ПлатежкаНомер="ЗАКР-1", ИНН="7702000002",
             Контр="Закрытый ООО"),
    ])
    f.on(_m(M_NOINN), [])
    f.on(_m(M_DUP), [
        ns(ИНН="7700000001", КПП="770001001", Код="00B1", Наим="Филиал-1"),
        ns(ИНН="7700000001", КПП="770002002", Код="00B2", Наим="Филиал-2"),
    ])
    f.on(_m(M_ACT), [ns(Дата=OLD, Номер="А-old", Контр="Старый", ИНН="7701234567")])
    f.on(_m(M_INV), [])
    f.on(_m(M_PAY), [])
    f.on(_m("// непроведённые: ПакетОбменСБанками"),
         [ns(Дата=OPEN, Номер="ПАК-1", Контр="-")])
    f.on(lambda t, p: "Документ.ПакетОбменСБанками" in t and not t.lstrip().startswith("//"),
         ns(Всего=10, Непров=1, Открыт=1))
    return f


def _full_main(fake, patch_base, capsys):
    from onec_audit.cli import main
    patch_base(fake)
    code = main(["--base", "x", "--locked-before", "2025-01-01"])
    out = capsys.readouterr().out
    marker = "ЧТО ПОСМОТРЕТЬ"
    assert marker in out, "сводка не напечатана"
    body, summ = out.split(marker, 1)
    return code, body, summ


# =====================================================================
# invariants
# =====================================================================
_WRITE_METHODS = {"Записать", "Провести", "Удалить", "УстановитьЗначение"}


def _write_calls(source):
    """Вызовы методов записи по AST: комментарии и строки (в т.ч. докстринги) не в счет."""
    bad = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in _WRITE_METHODS:
            bad.append((node.lineno, node.func.attr))
    return bad


@pytest.mark.invariants
def test_ac6_no_write_calls_in_source():
    """AC-6 / INV-AUDIT-01: в src/onec_audit/*.py нет .Записать/.Провести/.Удалить/.УстановитьЗначение."""
    files = sorted(SRC.glob("*.py"))
    assert files, f"исходники не найдены: {SRC}"
    found = {f.name: _write_calls(f.read_text(encoding="utf-8")) for f in files}
    found = {k: v for k, v in found.items() if v}
    assert not found, found


@pytest.mark.invariants
def test_ac6_detector_self_check():
    """AC-6 / INV-AUDIT-01: сам детектор видит вызов и не видит строки/комментарии."""
    assert _write_calls("x.Записать()\n")
    assert _write_calls('"""x.Провести()"""\n# y.Удалить()\ns = "z.УстановитьЗначение("\n') == []


@pytest.mark.invariants
def test_ac7_non_actionable_findings_not_in_summary(fake_base, patch_base, capsys):
    """AC-7 / INV-AUDIT-02: филиалы, служебные типы, сироты со списанием, закрытый период - только в теле."""
    _ = fake_base
    code, body, summ = _full_main(_rich_base(), patch_base, capsys)
    # подменная база дошла до всех случаев (иначе тест пуст)
    for tok in ("00B1", "ПакетОбменСБанками", "СИР-9", "ЗАКР-1", "А-old"):
        assert tok in body, f"случай {tok} не попал в тело: фикстура не дошла"
    assert "ВН-1" in summ, "устранимая находка должна быть в сводке"
    for tok in ("00B1", "00B2", "770002002", "ПакетОбменСБанками", "ПАК-1", "СИР-9",
                "ЗАКР-1", "Закрытый ООО", "А-old"):
        assert tok not in summ, f"{tok} не должен быть в сводке"
    assert "помечен" not in summ.lower()


@pytest.mark.invariants
def test_ac8_bank_orphan_disclaimer_in_same_section(fake_base, capsys):
    """AC-8 / INV-AUDIT-06: сирота (приблизительный признак) -> оговорка в той же секции."""
    from test_bank import DISCLAIMER, _orphan, _run
    body, _, _ = _run(fake_base, [_orphan(Номер="СИР-9")], capsys)
    assert "СИР-9" in body
    assert DISCLAIMER in body


@pytest.mark.invariants
def test_ac8_bank_attention_disclaimer_in_same_section(fake_base, capsys):
    """AC-8 / INV-AUDIT-06: платежка без списания -> оговорка про статус в той же секции."""
    from test_bank import DISCLAIMER, _row, _run
    body, _, _ = _run(fake_base, [_row()], capsys)
    assert "ПП-77" in body
    assert DISCLAIMER in body


@pytest.mark.invariants
def test_ac8_payment_sign_disclaimer_in_same_section(fake_base, capsys):
    """AC-8 / INV-AUDIT-06, 25: 'оплата ДА' -> оговорка с подстрокой 'хотя бы одна' в той же секции."""
    from test_sales_links import _run, _sales_unposted, _unposted
    _sales_unposted(fake_base, "СчетНаОплатуПокупателю",
                    ns(Дата=OPEN, Номер="С-5", Контр="К", Ссылка="ref-5"),
                    ns(Дата=datetime(2025, 3, 20)))
    body, _, _ = _run(_unposted(), fake_base, capsys, BOUNDARY)
    assert "ДА" in body
    assert "хотя бы одна" in body


@pytest.mark.invariants
def test_ac9_no_date_params_in_full_run(fake_base, patch_base, capsys):
    """AC-9 / INV-AUDIT-12: ни один запрос полного прогона не получает date/datetime параметром."""
    _ = fake_base
    seen: list[dict] = []
    _full_main(_rich_base(seen), patch_base, capsys)
    assert seen, "запросы не выполнялись"
    for params in seen:
        for k, v in params.items():
            assert not isinstance(v, (date, datetime)), f"параметр {k}={v!r}"


def _rows_filter_in_python(source):
    """Эвристика INV-AUDIT-13: отбор строк запроса в Python.

    Признаки: цикл по `.rows(...)` с телом `if ...: continue` в начале или телом из
    одного `if` без `else`; генератор/comprehension с `if` по `.rows(...)`.
    """
    def has_rows(node):
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "rows" for n in ast.walk(node))

    bad = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.For) and has_rows(node.iter):
            first = node.body[0]
            if isinstance(first, ast.If) and not first.orelse and \
                    all(isinstance(s, ast.Continue) for s in first.body):
                bad.append(node.lineno)
            elif len(node.body) == 1 and isinstance(first, ast.If) and not first.orelse:
                bad.append(node.lineno)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                if gen.ifs and has_rows(gen.iter):
                    bad.append(node.lineno)
    return sorted(set(bad))


@pytest.mark.invariants
def test_ac10_no_python_side_row_filtering_in_checks():
    """AC-10 / INV-AUDIT-13 (эвристика): в checks.py нет отбора строк rows() в Python."""
    src = (SRC / "checks.py").read_text(encoding="utf-8")
    assert _rows_filter_in_python(src) == []


@pytest.mark.invariants
def test_ac10_detector_self_check():
    """AC-10 / INV-AUDIT-13: эвристика ловит цикл-фильтр и comprehension, пропускает накопление."""
    assert _rows_filter_in_python("for r in b.rows(q):\n    if r.A:\n        continue\n    x(r)\n")
    assert _rows_filter_in_python("y = [r for r in b.rows(q) if r.A]\n")
    assert _rows_filter_in_python("for r in b.rows(q):\n    g[r.K].append(r)\n") == []


@pytest.mark.invariants
def test_ac11_cli_has_no_password_option():
    """AC-11 / INV-AUDIT-16: в CLI нет параметра пароля."""
    from onec_audit.cli import build_parser
    opts = [o for a in build_parser()._actions for o in a.option_strings]
    dests = [a.dest for a in build_parser()._actions]
    for name in opts + dests:
        low = name.lower()
        assert "pass" not in low and "pwd" not in low and "pw" not in low.replace("--", "")[:2], name


@pytest.mark.invariants
def test_ac11_password_argument_rejected(patch_base, capsys):
    """AC-11 / INV-AUDIT-16: --password в аргументах не принимается (код 2)."""
    from onec_audit.cli import main

    class Boom:
        def __getattr__(self, n):
            raise AssertionError("к базе обращаться нельзя")
    patch_base(Boom())
    assert main(["--base", "x", "--password", "secret"]) == 2


def _contains_secret(obj, secret, depth=0, seen=None):
    seen = set() if seen is None else seen
    if id(obj) in seen or depth > 6:
        return False
    seen.add(id(obj))
    if isinstance(obj, str):
        return secret in obj
    if isinstance(obj, bytes):
        return secret.encode("utf-8") in obj
    if isinstance(obj, dict):
        return any(_contains_secret(x, secret, depth + 1, seen)
                   for kv in obj.items() for x in kv)
    if isinstance(obj, (list, tuple, set, frozenset)):
        return any(_contains_secret(x, secret, depth + 1, seen) for x in obj)
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return _contains_secret(vars(obj), secret, depth + 1, seen)
    return False


@pytest.mark.invariants
def test_ac11_connection_string_not_stored(monkeypatch):
    """AC-11 / INV-AUDIT-16: при ONEC_PWD строка соединения (с паролем) не хранится в Base."""
    win32com_client = pytest.importorskip("win32com.client")
    secret = "S3cret-Pwd-424242"
    monkeypatch.setenv("ONEC_PWD", secret)
    calls: list = []

    class Any:
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)

            def f(*a, **k):
                calls.append((name, a, k))
                return Any()
            return f

    def fake_dispatch(*a, **k):
        calls.append(("Dispatch", a, k))
        return Any()

    monkeypatch.setattr(win32com_client, "Dispatch", fake_dispatch)
    monkeypatch.setattr("onec_audit.base.Dispatch", fake_dispatch, raising=False)
    from onec_audit.base import Base
    try:
        b = Base(r"C:\no\such\base", user="u", check_bits=False)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"не удалось подменить COM-подключение: {e!r}")
    if secret not in repr(calls):
        pytest.skip("пароль не дошел до подменного коннектора: сценарий не воспроизведен")
    assert not _contains_secret(b, secret), "пароль/строка соединения сохранены в Base"


@pytest.mark.invariants
def test_ac12_cp1251_stdout_and_file(fake_base, patch_base, monkeypatch, tmp_path):
    """AC-12 / INV-AUDIT-17: отчет с −, ё, «» печатается в cp1251 и сохраняется в файл без исключения."""
    from onec_audit.cli import main
    text = "значение −5, ё, «кавычки»"

    def emit(b, r, opt):
        r.head("Проверка − ё «»")
        r.w(text)
        r.finding("находка " + text)

    monkeypatch.setattr(checks_mod, "ALL_CHECKS", [emit])
    import onec_audit.cli as cli
    if hasattr(cli, "ALL_CHECKS"):
        monkeypatch.setattr(cli, "ALL_CHECKS", [emit])
    patch_base(fake_base)
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding="cp1251", newline="\n")
    monkeypatch.setattr(sys, "stdout", wrapper)
    out_file = tmp_path / "report.txt"
    code = main(["--base", "x", "--out", str(out_file)])
    wrapper.flush()
    raw_out = buf.getvalue()
    try:  # CLI вправе перенастроить поток (например, на utf-8) - важно, что не упал
        printed = raw_out.decode("utf-8")
    except UnicodeDecodeError:
        printed = raw_out.decode("cp1251", errors="replace")
    assert code == 1
    assert "ё" in printed and "находка" in printed
    assert "не выполнено проверок" not in printed
    raw = out_file.read_bytes()
    try:
        saved = raw.decode("utf-8")
    except UnicodeDecodeError:
        saved = raw.decode("cp1251")
    assert "ё" in saved and "находка" in saved


@pytest.mark.invariants
def test_ac13_marked_in_body_not_in_summary(fake_base, capsys):
    """AC-13 / INV-AUDIT-70: помеченные на удаление печатаются в теле, в сводку не идут."""
    fake_base.fill_names = True
    fake_base.on(lambda t, p: "ПометкаУдаления" in t and not t.lstrip().startswith("//"),
                 _LooseList())
    r = Report(echo=True)
    capsys.readouterr()
    checks_mod.marked_for_deletion(fake_base, r, Options())
    body = capsys.readouterr().out
    r.summary()
    summ = capsys.readouterr().out
    assert "помечен" in body.lower()
    assert "помечен" not in summ.lower()
    assert len(r.failures) == 0


@pytest.mark.invariants
def test_ac14_reclose_hint_when_not_closed(fake_base, capsys, monkeypatch):
    """AC-14 / INV-AUDIT-83: при 'не закрывался' в секции есть подсказка про перезакрытие задним числом."""
    monkeypatch.setattr(checks_mod, "months_to_check", lambda opt, today: [(2026, 2)])
    fake_base.names_by_kind["Документы"] = ["РегламентнаяОперация"]
    r = Report(echo=True)
    capsys.readouterr()
    checks_mod.month_closing(fake_base, r, Options())
    body = capsys.readouterr().out
    assert "не закрывался" in body
    low = body.lower()
    assert "задним числом" in low
    assert "перезакр" in low or "следующих" in low
