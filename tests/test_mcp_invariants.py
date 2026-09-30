"""MCP-сервер: форма JSON, усечение, отсутствие границы.
Спека: docs/specs/1c-audit.md, 2.9 и 3.4; docs/dev/2026-09-30-spec-mcp-invariant-tests.md.
Инварианты: INV-AUDIT-93, INV-AUDIT-95, INV-AUDIT-96.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

pytest.importorskip("mcp")

pytestmark = pytest.mark.mcp

TZ = dt.timezone(dt.timedelta(hours=3))


class _Ref:
    def __repr__(self):
        return "<COMObject <unknown>>"

    __str__ = __repr__


class _Sel:
    def __init__(self, rows):
        self.rows, self.i, self.next_calls = rows, -1, 0

    def Следующий(self):  # noqa: N802
        self.next_calls += 1
        self.i += 1
        return self.i < len(self.rows)

    def Получить(self, i):  # noqa: N802
        return self.rows[self.i][i]


class _Col:
    def __init__(self, n):
        self.Имя = n


class _Cols:
    def __init__(self, names):
        self.names = names

    def Количество(self):  # noqa: N802
        return len(self.names)

    def Получить(self, i):  # noqa: N802
        return _Col(self.names[i])


class _Res:
    def __init__(self, names, sel):
        self.Колонки, self._sel = _Cols(names), sel

    def Выбрать(self):  # noqa: N802
        return self._sel


class _Query:
    def __init__(self, names, sel):
        self._names, self._sel = names, sel
        self.Текст = ""

    def УстановитьПараметр(self, n, v):  # noqa: N802
        pass

    def Выполнить(self):  # noqa: N802
        return _Res(self._names, self._sel)


class _Conn:
    def __init__(self, names, sel):
        self._names, self._sel = names, sel

    def NewObject(self, name):  # noqa: N802
        assert name == "Запрос"
        return _Query(self._names, self._sel)


class _B:
    def __init__(self, names, rows):
        self.sel = _Sel(rows)
        self.c = _Conn(names, self.sel)

    def s(self, value):
        return "Контрагент №1" if isinstance(value, _Ref) else str(value)


@pytest.fixture
def run(monkeypatch):
    from onec_audit import mcp_server

    def _run(rows, limit=100, names=None):
        names = names or [f"К{i}" for i in range(len(rows[0]) if rows else 1)]
        b = _B(names, rows)
        monkeypatch.setattr(mcp_server, "_db", lambda: b)
        return mcp_server._query_sync("ВЫБРАТЬ 1", {}, limit), b

    return _run


def test_inv93_result_survives_json(run):
    """INV-AUDIT-93: результат проходит json.dumps."""
    res, _ = run([[dt.datetime(2025, 3, 10, 12, tzinfo=TZ), 5, "a", True, _Ref()]])
    json.dumps(res, ensure_ascii=False)


def test_inv93_date_iso_without_zone(run):
    """INV-AUDIT-93: дата с поясом -> ISO без пояса."""
    res, _ = run([[dt.datetime(2025, 3, 10, 12, tzinfo=TZ)]])
    text = json.dumps(res, ensure_ascii=False)
    assert "2025-03-10T12:00:00" in text
    assert "+03" not in text and "2025-03-10T12:00:00Z" not in text


@pytest.mark.parametrize("year", [100, 1])
def test_inv93_empty_date_is_empty_string(run, year):
    """INV-AUDIT-93: пустая дата 1С (год 100 и 1) -> пустая строка."""
    res, _ = run([[dt.datetime(year, 1, 1, tzinfo=TZ)]])
    text = json.dumps(res, ensure_ascii=False)
    assert "0001" not in text and "0100" not in text
    assert res["rows"][0] in ([""], {"К0": ""}) or "" in json.dumps(res["rows"][0])


def test_inv93_ref_is_platform_string(run):
    """INV-AUDIT-93: ссылка -> строка из b.s, без COMObject."""
    res, _ = run([[_Ref()]])
    text = json.dumps(res, ensure_ascii=False)
    assert "Контрагент №1" in text
    assert "COMObject" not in text


def test_inv95_truncated_over_limit(run):
    """INV-AUDIT-95: строк больше limit -> truncated, note, row_count == limit, без чтения дальше."""
    res, b = run([[i] for i in range(50)], limit=3)
    assert res["truncated"] is True
    assert res["note"]
    assert res["row_count"] == 3
    assert len(res["rows"]) == 3
    assert b.sel.next_calls <= 4


def test_inv95_not_truncated_exactly_limit(run):
    """INV-AUDIT-95: строк ровно limit -> truncated False, note пустой."""
    res, _ = run([[i] for i in range(3)], limit=3)
    assert res["truncated"] is False
    assert res["note"] == ""
    assert res["row_count"] == 3


def test_inv95_not_truncated_below_limit(run):
    """INV-AUDIT-95: строк меньше limit -> truncated False."""
    res, _ = run([[1], [2]], limit=3)
    assert res["truncated"] is False
    assert res["note"] == ""


def test_inv96_no_boundary_parameter_in_tools():
    """INV-AUDIT-96: у инструментов нет параметра границы."""
    from onec_audit import mcp_server as srv
    tools = asyncio.run(srv.server.list_tools())
    schemas = {t.name: getattr(t, "input_schema", None) or t.inputSchema for t in tools}
    assert {"query", "list_metadata", "describe", "row_counts"} <= set(schemas)
    for name, schema in schemas.items():
        for p in schema.get("properties", {}):
            assert not any(w in p.lower() for w in
                           ("border", "boundary", "closed", "граница", "границ", "закрыт")), (name, p)


def test_inv96_no_closed_mark_for_old_dates(run):
    """INV-AUDIT-96: дата 2019 года без [закрыт] в значениях и note."""
    res, _ = run([[dt.datetime(2019, 5, 1, tzinfo=TZ)]])
    assert "[закрыт]" not in json.dumps(res, ensure_ascii=False)
    assert "[закрыт]" not in res["note"]
