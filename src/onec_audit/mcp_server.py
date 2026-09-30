# -*- coding: utf-8 -*-
"""MCP-сервер для чтения базы 1С:Предприятие 8.3 через V83.COMConnector.

ТОЛЬКО ЧТЕНИЕ — по построению, а не по договорённости.
Здесь нет ни одного инструмента, который пишет в базу. Из платформы
используются ровно две вещи: Запрос.Выполнить() и обход Метаданных.
Вызовов .Записать(), .Провести(), .Удалить(), .УстановитьЗначение()
в модуле нет, и добавлять их нельзя — на этом держится всё обещание
проекта. Библиотека onec_audit.base таких методов тоже не содержит.

Инструменты:
    query        — выполнить запрос на языке запросов 1С
    list_metadata— список объектов метаданных заданного вида
    describe     — структура одного объекта метаданных
    row_counts   — число записей в таблицах

Подключение — через переменные окружения, чтобы пути и пароли
не лежали в конфиге MCP-клиента:
    ONEC_BASE            каталог файловой базы
    ONEC_SRVR + ONEC_REF клиент-серверная база
    ONEC_USER, ONEC_PWD  если база с пользователями

Запуск:
    python -m onec_audit.mcp_server

Пример конфигурации MCP-клиента:
    {"command": "python", "args": ["-m", "onec_audit.mcp_server"],
     "env": {"PYTHONPATH": "C:/path/to/1c-audit/src",
             "ONEC_BASE": "C:/path/to/base",
             "PYTHONIOENCODING": "utf-8"}}
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import functools
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
try:
    from mcp.server.mcpserver.exceptions import ToolError  # mcp 2.x
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

try:
    from .base import Base
except ImportError:  # запуск файлом, а не модулем пакета
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from onec_audit.base import Base


# --------------------------------------------------------------- виды объектов
# Английское имя вида -> (коллекция метаданных, префикс таблицы в запросе).
# Префикс "" означает, что объект запросом не читается (обработки, отчёты).
KINDS: dict[str, tuple[str, str]] = {
    "catalogs": ("Справочники", "Справочник"),
    "documents": ("Документы", "Документ"),
    "enums": ("Перечисления", "Перечисление"),
    "information_registers": ("РегистрыСведений", "РегистрСведений"),
    "accumulation_registers": ("РегистрыНакопления", "РегистрНакопления"),
    "accounting_registers": ("РегистрыБухгалтерии", "РегистрБухгалтерии"),
    "data_processors": ("Обработки", ""),
    "reports": ("Отчеты", ""),
    "constants": ("Константы", "Константа"),
}

KIND_LIST = ", ".join(KINDS)


# ------------------------------------------------------- один поток для COM
# COM-объекты 1С апартаментные: соединение, открытое в одном потоке, из
# другого потока напрямую не работает. MCP SDK крутит инструменты в пуле,
# поэтому ВСЯ работа с COM уходит на один выделенный поток с собственным
# CoInitialize. Соединение при этом открывается однажды и переиспользуется.
_pool: ThreadPoolExecutor | None = None
_base: Base | None = None


def _init_com_thread() -> None:
    import pythoncom

    pythoncom.CoInitialize()


def _com_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="onec-com", initializer=_init_com_thread
        )
    return _pool


async def _in_com(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Выполнить работу с COM на выделенном потоке."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_com_pool(), functools.partial(fn, *args, **kwargs))


def _connect() -> Base:
    """Открыть соединение по переменным окружения. Вызывается на COM-потоке."""
    base = os.environ.get("ONEC_BASE")
    srvr = os.environ.get("ONEC_SRVR")
    ref = os.environ.get("ONEC_REF")
    if not base and not srvr:
        raise ToolError(
            "не задана база. Укажите переменные окружения:\n"
            "  ONEC_BASE=C:\\путь\\к\\файловой\\базе\n"
            "  либо ONEC_SRVR=сервер и ONEC_REF=имя_базы\n"
            "  при необходимости ONEC_USER и ONEC_PWD"
        )
    if srvr and not ref:
        raise ToolError("с ONEC_SRVR нужен и ONEC_REF (имя базы на сервере)")
    return Base(
        base,
        srvr=srvr,
        ref=ref,
        user=os.environ.get("ONEC_USER"),
        password=os.environ.get("ONEC_PWD"),
    )


def _db() -> Base:
    """Соединение, открытое лениво и переиспользуемое."""
    global _base
    if _base is None:
        try:
            _base = _connect()
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"не удалось подключиться к 1С: {_readable_error(exc)}") from exc
    return _base


def _release() -> None:
    """Отпустить соединение. Повторный вызов безопасен."""
    global _base
    base, _base = _base, None
    if base is not None:
        base.close()


def _release_com() -> None:
    """Отпустить соединение и COM-поток. Выполняется на COM-потоке."""
    import pythoncom

    try:
        _release()
    finally:
        pythoncom.CoUninitialize()


def _shutdown_pool() -> None:
    """Освободить соединение на COM-потоке, затем остановить пул.

    Порядок важен: после остановки пула освободить соединение на его потоке
    уже нельзя, COM-объекты гаснут на чужом потоке, и на выходе печатается
    «Win32 exception occurred releasing IUnknown». Повторный вызов безопасен.
    """
    global _pool
    pool, _pool = _pool, None
    if pool is None:
        return
    try:
        pool.submit(_release_com).result(timeout=5)
    except Exception:
        pass
    pool.shutdown(wait=True)


@contextlib.asynccontextmanager
async def _lifespan(_server: Any):
    try:
        yield
    finally:
        _shutdown_pool()


# ------------------------------------------------------------- преобразования
def _value(b: Base, value: Any) -> Any:
    """Значение поля запроса -> то, что переживёт JSON.

    Даты приводятся к ISO без часового пояса: в 1С дата хранится без него,
    а COM подсовывает свой (см. datetime_literal() в base.py). Пустая дата
    1С (0001-01-01) приезжает через COM с годом 100 — отдаём пустую строку,
    иначе в отчёте появляются даты вида 0100-01-01.

    Ссылки и перечисления COM отдаёт объектами, str() по ним даёт
    '<COMObject <unknown>>' — поэтому только через Base.s().
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, dt.datetime):
        naive = value.replace(tzinfo=None)
        return "" if naive.year < 1900 else naive.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return b.s(value)


def _type_names(b: Base, attribute: Any) -> list[str]:
    """Имена типов реквизита. Составной тип даёт несколько имён."""
    try:
        types = attribute.Тип.Типы()
        return [b.s(types.Получить(i)) for i in range(types.Количество())]
    except Exception:
        return [b.s(attribute.Тип)]


def _required(b: Base, attribute: Any) -> tuple[bool | None, str | None]:
    """Обязательность = ПроверкаЗаполнения реквизита равна ВыдаватьОшибку.

    Эталон берётся у самой платформы (системное перечисление глобального
    контекста), а не строкой в коде. Не удалось прочитать - (None, пояснение),
    а не молчаливое False.
    """
    try:
        return bool(attribute.ПроверкаЗаполнения == b.c.ПроверкаЗаполнения.ВыдаватьОшибку), None
    except Exception as exc:
        return None, f"обязательность не прочитана: {_readable_error(exc)}"


def _attribute(b: Base, attribute: Any) -> dict[str, Any]:
    """Описание одного реквизита / измерения / ресурса."""
    types = _type_names(b, attribute)
    required, note = _required(b, attribute)
    return {
        "name": str(attribute.Имя),
        "synonym": str(attribute.Синоним),
        "type": ", ".join(types),
        "types": types,
        "required": required,
        "note": note,
    }


def _sub_collection(b: Base, owner: Any, name: str) -> list[dict[str, Any]]:
    """Реквизиты вложенной коллекции метаданных, если она есть у объекта.

    Часть коллекций через COM-соединение недоступна (СтандартныеРеквизиты,
    ПризнакиУчета) — обращение к ним падает, а не отдаёт пустоту, поэтому
    здесь широкий except: отсутствие коллекции не должно ломать describe.
    """
    try:
        collection = getattr(owner, name)
        return [_attribute(b, collection.Получить(i))
                for i in range(collection.Количество())]
    except Exception:
        return []


def _template_names(owner: Any) -> list[str]:
    try:
        templates = owner.Макеты
        return [str(templates.Получить(i).Имя) for i in range(templates.Количество())]
    except Exception:
        return []


_RAW_COM = re.compile(r"pywintypes|\(-?\d{6,}\s*,")


def _readable_error(exc: Exception) -> str:
    """Текст ошибки для человека, без сырого COM-кортежа.

    Платформа кладёт внятное сообщение («Поле не найдено "Дата"» с указанием
    места в тексте запроса) глубоко в кортеж excepinfo, а str(com_error)
    показывает лишь «(-2147352567, 'Ошибка.', (...))». Модели нужен именно
    текст 1С: по нему запрос можно исправить с первой попытки. Если текста
    1С нет, а в сообщении остался сырой кортеж, отдаём только код.
    """
    excepinfo = getattr(exc, "excepinfo", None)
    if excepinfo:
        for item in excepinfo:
            if isinstance(item, str) and ("\n" in item or "не найден" in item):
                return item.strip()
        for item in excepinfo:
            if isinstance(item, str) and item and item != "Ошибка.":
                return item.strip()
    text = str(exc)
    if _RAW_COM.search(text):
        code = re.search(r"-?\d{6,}", text)
        return "ошибка COM при обращении к 1С" + (f" (код {code.group()})" if code else "")
    return text


def _tool_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Любая ошибка внутри -> ToolError с читаемым текстом."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(_readable_error(exc)) from exc
    return wrapper


def _collection(b: Base, kind: str) -> tuple[Any, str]:
    """Коллекция метаданных и префикс таблицы по английскому имени вида."""
    if kind not in KINDS:
        raise ToolError(f"kind должен быть одним из: {KIND_LIST}. Получено: {kind!r}")
    russian, prefix = KINDS[kind]
    return getattr(b.md, russian), prefix


# ------------------------------------------------------- защита от не-SELECT
_FORBIDDEN = re.compile(r"\b(УНИЧТОЖИТЬ|DROP)\b", re.IGNORECASE)
_STARTS_SELECT = re.compile(r"(ВЫБРАТЬ|SELECT)\b", re.IGNORECASE)


def _check_select_only(text: str) -> None:
    """Пропустить только запрос, начинающийся с ВЫБРАТЬ/SELECT.

    Язык запросов 1С менять данные не умеет в принципе — это read-only
    язык, INSERT/UPDATE/DELETE в нём нет. Единственное исключение —
    УНИЧТОЖИТЬ (DROP) для временных таблиц, поэтому оно отклоняется,
    а сам запрос обязан начинаться с ВЫБРАТЬ: ведущие пробелы, переводы
    строк и строчные комментарии // при этом пропускаются.

    Проверка нужна не от порчи данных (её через запрос не сделать),
    а чтобы инструмент нельзя было использовать не по назначению —
    и чтобы это было видно из кода.
    """
    if not text or not text.strip():
        raise ToolError("пустой текст запроса")
    if _FORBIDDEN.search(text):
        raise ToolError(
            "отклонено: в тексте есть УНИЧТОЖИТЬ/DROP. "
            "Сервер работает только на чтение, допускается лишь ВЫБРАТЬ."
        )
    head = text
    while True:  # снять пробелы и строчные комментарии перед первым словом
        head = head.lstrip()
        if head.startswith("//"):
            head = head.split("\n", 1)[1] if "\n" in head else ""
            continue
        break
    if not _STARTS_SELECT.match(head):
        first = (head.split(None, 1) or ["(пусто)"])[0]
        raise ToolError(
            f"отклонено: запрос должен начинаться с ВЫБРАТЬ (или SELECT), "
            f"а начинается с {first!r}. Сервер работает только на чтение."
        )


# ------------------------------------------------------------------- сервер
READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)

server = MCPServer(
    "onec-audit",
    title="1С:Предприятие — чтение базы",
    instructions=(
        "Доступ к базе 1С:Предприятие 8.3 только на чтение: запросы и метаданные. "
        "Записать, провести или удалить что-либо этими инструментами невозможно.\n\n"
        "Порядок работы, который экономит время: сначала list_metadata, чтобы "
        "узнать точные имена объектов, затем describe для структуры, и только "
        "потом query. Имена метаданных в 1С угадывать нельзя — это главная "
        "причина ошибок: например, перечисление статусов обмена с банками "
        "называется СтатусыОбменСБанками, без «а» после «Обмен».\n\n"
        "Даты в запросах передавайте литералом ДАТАВРЕМЯ(год,месяц,день) "
        "в тексте, а не параметром: параметр-дата приезжает в 1С сдвинутой "
        "на часовой пояс, и это тихая ошибка без исключения."
    ),
    version="0.1.0",
    lifespan=_lifespan,
)


@server.tool(
    annotations=READ_ONLY,
    description=(
        "Выполнить запрос на языке запросов 1С и вернуть строки результата.\n\n"
        "ТОЛЬКО ЧТЕНИЕ. Принимаются лишь запросы, начинающиеся с ВЫБРАТЬ "
        "(или SELECT); текст с УНИЧТОЖИТЬ/DROP отклоняется. Язык запросов 1С "
        "и так не умеет менять данные — операторов INSERT/UPDATE/DELETE в нём "
        "нет, — так что записать что-либо через этот инструмент невозможно.\n\n"
        "ДАТЫ ПАРАМЕТРАМИ ПЕРЕДАВАТЬ НЕЛЬЗЯ. Дата, отданная параметром, "
        "маршалится с локальным часовым поясом и приезжает в 1С сдвинутой: "
        "1 февраля 2025 00:00:00 доходит как 31 января 2025 21:00:00 (МСК). "
        "Исключения при этом нет — просто неверные цифры, и документы конца "
        "месяца попадают в соседний месяц. Правильно так:\n"
        "  ГДЕ Дата >= ДАТАВРЕМЯ(2025,2,1) И Дата <= ДАТАВРЕМЯ(2025,2,28,23,59,59)\n"
        "Где даты можно избежать вовсе — берите ГОД(Дата) и МЕСЯЦ(Дата) "
        "целыми параметрами: ГДЕ ГОД(Дата) = &Год И МЕСЯЦ(Дата) = &Месяц.\n\n"
        "Параметры (params) годятся для чисел, строк, булевых. Ссылки задавайте "
        "в тексте через ЗНАЧЕНИЕ(Справочник.Контрагенты.ПустаяСсылка) или "
        "ЗНАЧЕНИЕ(Перечисление.СтатусыОбменСБанками.Исполнен).\n\n"
        "Ссылки и перечисления в результате приводятся к строке. Дешевле "
        "и точнее просить это у самой 1С: ПРЕДСТАВЛЕНИЕ(Контрагент) КАК "
        "КонтрагентТекст или Контрагент.Код. Даты возвращаются ISO-строками, "
        "пустая дата 1С — пустой строкой.\n\n"
        "Полные имена таблиц: Справочник.Контрагенты, "
        "Документ.РеализацияТоваровУслуг, РегистрСведений.КурсыВалют, "
        "РегистрБухгалтерии.Хозрасчетный.Остатки(, , , ). Точные имена "
        "берите из list_metadata, не угадывайте."
    ),
)
async def query(
    text: str,
    params: dict[str, Any] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Запрос к базе 1С. text — текст запроса, params — параметры, limit — сколько строк отдать."""
    _check_select_only(text)
    if limit < 1:
        raise ToolError("limit должен быть положительным")
    return await _in_com(_query_sync, text, params or {}, limit)


@_tool_errors
def _query_sync(text: str, params: dict[str, Any], limit: int) -> dict[str, Any]:
    for name, value in params.items():  # до обращения к 1С
        if not isinstance(value, (bool, int, float, str)):
            raise ToolError(
                f"параметр {name!r}: допустимы только число, строка, булево, "
                f"получено {type(value).__name__}. Ссылки задавайте в тексте через ЗНАЧЕНИЕ(...)"
            )
    b = _db()
    q = b.c.NewObject("Запрос")
    q.Текст = text
    for name, value in params.items():
        q.УстановитьПараметр(name, value)
    try:
        result = q.Выполнить()
    except Exception as exc:
        raise ToolError(f"1С отклонила запрос: {_readable_error(exc)}") from exc

    # Имена колонок берём у результата: так они совпадают с КАК-псевдонимами
    # запроса, а значения читаются по индексу — на один COM-вызов меньше.
    columns = result.Колонки
    fields = [str(columns.Получить(i).Имя) for i in range(columns.Количество())]

    rows: list[dict[str, Any]] = []
    selection = result.Выбрать()
    truncated = False
    while selection.Следующий():
        if len(rows) >= limit:
            truncated = True  # курсор дальше не читаем, лишнего трафика нет
            break
        rows.append({
            name: _value(b, selection.Получить(i)) for i, name in enumerate(fields)
        })
    return {
        "fields": fields,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "note": (f"отдано первых {limit} строк, в результате есть ещё — "
                 "поднимите limit или уточните отбор") if truncated else "",
    }


@server.tool(
    annotations=READ_ONLY,
    description=(
        "Список объектов метаданных заданного вида: имена и синонимы.\n\n"
        "НАЧИНАЙТЕ РАБОТУ С ЭТОГО. Имена объектов в 1С угадывать нельзя — "
        "это главная причина ошибок в запросах. Синоним (то, что видит "
        "пользователь) и имя (то, что нужно в запросе) часто расходятся: "
        "документ «Счет покупателю» называется СчетНаОплатуПокупателю.\n\n"
        f"kind — один из: {KIND_LIST}.\n\n"
        "pattern — регулярное выражение (Python, без учёта регистра) для "
        "фильтра по имени и синониму. В типовой конфигурации объектов много "
        "(порядка 740 справочников и 437 документов), поэтому фильтр почти "
        "всегда нужен: pattern='контрагент' найдёт всё про контрагентов.\n\n"
        "В ответе full_name — готовое имя таблицы для query. У обработок "
        "и отчётов его нет: запросом они не читаются."
    ),
)
async def list_metadata(kind: str, pattern: str | None = None) -> dict[str, Any]:
    """Список объектов метаданных. kind — вид объектов, pattern — фильтр по имени."""
    if kind not in KINDS:
        raise ToolError(f"kind должен быть одним из: {KIND_LIST}. Получено: {kind!r}")
    regex: re.Pattern[str] | None = None
    if pattern:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ToolError(f"pattern не является регулярным выражением: {exc}") from exc
    return await _in_com(_list_metadata_sync, kind, regex)


@_tool_errors
def _list_metadata_sync(kind: str, regex: re.Pattern[str] | None) -> dict[str, Any]:
    b = _db()
    collection, prefix = _collection(b, kind)
    total = collection.Количество()
    items = []
    for i in range(total):
        obj = collection.Получить(i)
        name, synonym = str(obj.Имя), str(obj.Синоним)
        if regex and not (regex.search(name) or regex.search(synonym)):
            continue
        item: dict[str, Any] = {"name": name, "synonym": synonym}
        if prefix:
            item["full_name"] = f"{prefix}.{name}"
        items.append(item)
    return {
        "kind": kind,
        "total_in_config": total,
        "returned": len(items),
        "items": items,
    }


@server.tool(
    annotations=READ_ONLY,
    description=(
        "Структура одного объекта метаданных: из чего он состоит.\n\n"
        "Вызывайте перед тем, как писать запрос: здесь видны точные имена "
        "полей и их типы, а значит не придётся угадывать.\n\n"
        f"kind — один из: {KIND_LIST}. name — имя объекта (не синоним!), "
        "как его отдал list_metadata.\n\n"
        "Возвращается: синоним; реквизиты с типами и признаком обязательности "
        "(required — заполнение проверяется платформой); табличные части "
        "со своими реквизитами (в запросе это Документ.Имя.ИмяТабличнойЧасти); "
        "макеты. Для регистров вместо реквизитов — измерения, ресурсы "
        "и реквизиты по отдельности. Для перечислений — список значений "
        "(в запросе ЗНАЧЕНИЕ(Перечисление.Имя.ИмяЗначения)).\n\n"
        "Составной реквизит даёт несколько типов в types."
    ),
)
async def describe(kind: str, name: str) -> dict[str, Any]:
    """Структура объекта метаданных. kind — вид, name — имя объекта."""
    if kind not in KINDS:
        raise ToolError(f"kind должен быть одним из: {KIND_LIST}. Получено: {kind!r}")
    if not name:
        raise ToolError("нужно имя объекта (name)")
    return await _in_com(_describe_sync, kind, name)


@_tool_errors
def _describe_sync(kind: str, name: str) -> dict[str, Any]:
    b = _db()
    collection, prefix = _collection(b, kind)
    obj = b.find(collection, name)
    if obj is None:
        # Подсказать похожие: имя почти всегда «почти угадано».
        similar = [n for n in b.names(collection) if name.lower() in n.lower()][:15]
        raise ToolError(
            f"объекта {kind}/{name!r} в конфигурации нет."
            + (f" Похожие имена: {', '.join(similar)}" if similar
               else " Снимите список через list_metadata.")
        )

    info: dict[str, Any] = {
        "kind": kind,
        "name": str(obj.Имя),
        "synonym": str(obj.Синоним),
    }
    if prefix:
        info["full_name"] = f"{prefix}.{obj.Имя}"

    if kind == "enums":
        values = obj.ЗначенияПеречисления
        info["values"] = [
            {"name": str(values.Получить(i).Имя),
             "synonym": str(values.Получить(i).Синоним)}
            for i in range(values.Количество())
        ]
        return info

    if kind == "constants":
        info["types"] = _type_names(b, obj)
        return info

    if kind.endswith("_registers"):
        info["dimensions"] = _sub_collection(b, obj, "Измерения")
        info["resources"] = _sub_collection(b, obj, "Ресурсы")
        info["attributes"] = _sub_collection(b, obj, "Реквизиты")
        for field, attr in (("periodicity", "Периодичность"),
                            ("write_mode", "РежимЗаписи")):
            try:
                info[field] = b.s(getattr(obj, attr))
            except Exception:
                pass
        return info

    # справочники, документы, обработки, отчёты
    info["attributes"] = _sub_collection(b, obj, "Реквизиты")
    sections = []
    try:
        parts = obj.ТабличныеЧасти
        for i in range(parts.Количество()):
            part = parts.Получить(i)
            sections.append({
                "name": str(part.Имя),
                "synonym": str(part.Синоним),
                "attributes": _sub_collection(b, part, "Реквизиты"),
            })
    except Exception:
        pass
    info["tabular_sections"] = sections
    info["templates"] = _template_names(obj)
    return info


@server.tool(
    annotations=READ_ONLY,
    description=(
        "Сколько записей в таблицах. Быстрый способ понять, что в базе живёт, "
        "а что заведено и не используется.\n\n"
        "full_names — список полных имён таблиц: "
        "['Документ.РеализацияТоваровУслуг', 'РегистрСведений.КурсыВалют', "
        "'Справочник.Контрагенты'].\n\n"
        f"kind (один из: {KIND_LIST}) — вместо списка посчитать по ВСЕМ "
        "объектам этого вида. ОСТОРОЖНО: это отдельный запрос на каждый "
        "объект — сотни запросов и десятки секунд (в типовой конфигурации "
        "740 справочников, 437 документов, больше 1300 регистров сведений). "
        "Если нужен не весь вид, отберите имена через list_metadata "
        "и передайте их в full_names.\n\n"
        "Ответ - словарь по именам: {count, reason}. count = -1 означает, что "
        "таблица не посчитана (неверное имя, нет в метаданных или запросом не "
        "читается), причина в reason; это не значит, что она пуста."
    ),
)
async def row_counts(
    full_names: list[str] | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Число записей в таблицах. full_names — список имён, kind — весь вид объектов."""
    if not full_names and not kind:
        raise ToolError("нужен либо full_names (список полных имён), либо kind")
    if kind and kind not in KINDS:
        raise ToolError(f"kind должен быть одним из: {KIND_LIST}. Получено: {kind!r}")
    return await _in_com(_row_counts_sync, full_names or [], kind)


_TABLE_NAME = re.compile(
    r"^(Справочник|Документ|РегистрСведений|РегистрНакопления|РегистрБухгалтерии|"
    r"ПланСчетов|ПланВидовХарактеристик|Перечисление|Константа)\.[A-Za-zА-Яа-яЁё0-9_]+$"
)

# префикс таблицы в запросе -> коллекция метаданных
_PREFIX_COLLECTION = {
    "Справочник": "Справочники",
    "Документ": "Документы",
    "РегистрСведений": "РегистрыСведений",
    "РегистрНакопления": "РегистрыНакопления",
    "РегистрБухгалтерии": "РегистрыБухгалтерии",
    "ПланСчетов": "ПланыСчетов",
    "ПланВидовХарактеристик": "ПланыВидовХарактеристик",
    "Перечисление": "Перечисления",
    "Константа": "Константы",
}


def _valid_table_name(name: str) -> bool:
    """Имя таблицы допустимого вида. Только такое имя попадает в текст запроса."""
    return isinstance(name, str) and _TABLE_NAME.fullmatch(name) is not None


@_tool_errors
def _row_counts_sync(names: list[str], kind: str | None = None) -> dict[str, dict[str, Any]]:
    b = _db()
    tables = list(names)
    if kind:
        collection, prefix = _collection(b, kind)
        if not prefix:
            raise ToolError(
                f"объекты вида {kind} запросом не читаются, считать в них нечего"
            )
        tables += [f"{prefix}.{n}" for n in b.names(collection)]

    result: dict[str, dict[str, Any]] = {}
    for table in tables:
        if not _valid_table_name(table):
            result[table] = {"count": -1, "reason": "недопустимое имя таблицы: "
                             "ожидается Вид.Имя, например Справочник.Контрагенты"}
            continue
        prefix, name = table.split(".", 1)
        if b.find(getattr(b.md, _PREFIX_COLLECTION[prefix]), name) is None:
            result[table] = {"count": -1, "reason": "объекта нет в метаданных"}
            continue
        count = b.count(table)
        result[table] = {
            "count": count,
            "reason": None if count >= 0 else "таблица не читается запросом",
        }
    return result


# --------------------------------------------------------------------- запуск
def main() -> None:
    try:
        server.run("stdio")
    finally:
        _shutdown_pool()


if __name__ == "__main__":
    main()
