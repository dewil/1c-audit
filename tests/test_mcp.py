"""MCP-сервер. Спека: docs/dev/2026-09-30-spec-runtime.md, часть 2.
Инварианты: INV-AUDIT-90, INV-AUDIT-91, INV-AUDIT-92, INV-AUDIT-94.
"""
from __future__ import annotations

import types

import pytest

pytest.importorskip("mcp")
try:
    from mcp.server.mcpserver.exceptions import ToolError  # mcp 2.x
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError

pytestmark = pytest.mark.mcp


@pytest.fixture
def srv(monkeypatch, fake_base):
    from onec_audit import mcp_server
    monkeypatch.setattr(mcp_server, "_base", fake_base, raising=False)
    monkeypatch.setattr(mcp_server, "_connect", lambda *a, **k: fake_base)
    return mcp_server


def test_ac8_valid_table_name(srv):
    """AC-8 / INV-AUDIT-92: проверка имени таблицы."""
    assert srv._valid_table_name("Справочник.Контрагенты") is True
    assert srv._valid_table_name("Справочник.Контрагенты; УНИЧТОЖИТЬ Т") is False
    assert srv._valid_table_name("Справочник.Контрагенты ГДЕ 1=1") is False


def test_ac9_row_counts_invalid_name_no_query(srv, fake_base):
    """AC-9 / INV-AUDIT-92: неверное имя -> -1 и причина, запросов нет."""
    bad = "Справочник.Контрагенты; УНИЧТОЖИТЬ Т"
    res = srv._row_counts_sync([bad])
    assert fake_base.queries == []
    assert res[bad]["count"] == -1
    assert res[bad]["reason"]


def test_ac10_query_list_param_toolerror_before_1c(srv, fake_base):
    """AC-10 / INV-AUDIT-91: параметр-список -> ToolError до обращения к 1С."""
    with pytest.raises(ToolError):
        srv._query_sync("ВЫБРАТЬ 1", {"П": [1, 2]}, 10)
    assert fake_base.queries == []


@pytest.mark.parametrize("stage", ["param", "connect"])
def test_ac11_errors_become_toolerror(srv, monkeypatch, fake_base, stage):
    """AC-11 / INV-AUDIT-94: сырая COM-ошибка -> ToolError, без pywintypes."""
    raw = RuntimeError("pywintypes.com_error: (-2147352567, 'Ошибка', (0, 'x'), None)")
    if stage == "connect":
        def boom(*a, **k):
            raise raw
        monkeypatch.setattr(srv, "_connect", boom)
        monkeypatch.setattr(srv, "_base", None, raising=False)
    else:
        fake_base.raise_all = True
        fake_base.error = raw
    with pytest.raises(ToolError) as e:
        srv._query_sync("ВЫБРАТЬ 1", {"П": 1}, 10)
    assert "pywintypes" not in str(e.value)
    assert "(-2147352567" not in str(e.value)


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
        if isinstance(self._fill, BaseException):
            raise self._fill
        return self._fill

    def __getattr__(self, name):
        return _Attrs()


_OK = types.SimpleNamespace(name="ВыдаватьОшибку")
_NO = types.SimpleNamespace(name="НеПроверять")


def _describe(srv, fake_base, fill, enum_raises=False):
    obj = types.SimpleNamespace(
        Имя="Контрагенты", Синоним="К", Реквизиты=_Attrs([_Attr(fill)]),
        ТабличныеЧасти=_Attrs(), Измерения=_Attrs(), Ресурсы=_Attrs(), Макеты=_Attrs(),
    )
    fake_base.find = lambda coll, name: obj
    if enum_raises:
        class Boom:
            @property
            def ПроверкаЗаполнения(self):  # noqa: N802
                raise RuntimeError("нет перечисления")
        conn = Boom()
    else:
        conn = types.SimpleNamespace(
            ПроверкаЗаполнения=types.SimpleNamespace(ВыдаватьОшибку=_OK, НеПроверять=_NO))
    fake_base.c = conn
    fake_base.ПроверкаЗаполнения = conn.ПроверкаЗаполнения if not enum_raises else None
    return srv._describe_sync("catalogs", "Контрагенты")


def _required(res):
    assert res["attributes"], res
    return res["attributes"][0]


def test_ac12_required_true(srv, fake_base):
    """AC-12 / INV-AUDIT-90: ВыдаватьОшибку -> required true."""
    assert _required(_describe(srv, fake_base, _OK))["required"] is True


def test_ac12_required_false(srv, fake_base):
    """AC-12 / INV-AUDIT-90: значение НеПроверять -> required false.

    переписан под spec-hardening (К-19, AC-5): в подменном системном перечислении
    есть НеПроверять, false только при равенстве ему, а не при любом "ином".
    """
    assert _required(_describe(srv, fake_base, _NO))["required"] is False


def test_ac12_required_null_on_read_failure(srv, fake_base):
    """AC-12 / INV-AUDIT-90: чтение бросает -> required null и пояснение."""
    item = _required(_describe(srv, fake_base, RuntimeError("не прочитать")))
    assert item["required"] is None
    assert item["note"]


def test_ac13_release_twice(srv):
    """AC-13 / INV-AUDIT-92: повторное освобождение безопасно."""
    srv._release()
    srv._release()


def test_ac12_required_null_when_enum_unreadable(srv, fake_base):
    """AC-12 / INV-AUDIT-90: системное перечисление не читается -> required null, note."""
    item = _required(_describe(srv, fake_base, _OK, enum_raises=True))
    assert item["required"] is None
    assert item["note"]
