# -*- coding: utf-8 -*-
"""Подключение к базе 1С:Предприятие 8.3 через V83.COMConnector. Только чтение.

Здесь собрана вся обвязка над COM и все грабли, на которые можно наступить.
Подробный разбор — docs/com-traps.md.

Ничего не пишет: доступны только Запрос.Выполнить() и обход метаданных.
Методов записи (.Записать, .Провести, .Удалить) в библиотеке нет
и добавлять их не следует — на этом строится обещание безопасности.
"""
from __future__ import annotations

import datetime as dt
import os
import struct
from typing import Any, Iterator

try:
    import win32com.client
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Нужен pywin32:  py -3.12 -m pip install pywin32"
    ) from exc


class BitnessError(RuntimeError):
    """Разрядность процесса не совпадает с зарегистрированным comcntr.dll."""


class ConnectError(RuntimeError):
    """Не удалось подключиться к базе."""


def check_bitness() -> None:
    """Процесс обязан совпадать по битности с comcntr.dll.

    Несовпадение даёт «Класс не зарегистрирован» — сообщение, которое уводит
    в сторону: выглядит как отсутствие библиотеки, а на деле разрядность.
    64-битная платформа 1С — самый частый случай, поэтому проверяем на x64.
    """
    if struct.calcsize("P") * 8 != 64:
        raise BitnessError(
            "Нужен 64-битный Python: зарегистрированный comcntr.dll 64-битный. "
            "Для 32-битной платформы 1С нужен 32-битный Python и её собственный comcntr.dll."
        )


def datetime_literal(moment: dt.datetime) -> str:
    """Литерал ДАТАВРЕМЯ(...) для подстановки в ТЕКСТ запроса.

    ЗАЧЕМ ЭТО ВООБЩЕ НУЖНО. Передавать дату параметром нельзя: naive datetime
    марашлится с локальным часовым поясом и приезжает в 1С сдвинутым.

        datetime(2025, 2, 1, 0, 0, 0)      -> 1С видит 2025-01-31 21:00:00 (UTC+3)
        pywintypes.Time(то же самое)       -> тот же сдвиг
        ДАТАВРЕМЯ(2025,2,1,0,0,0) в тексте -> 1С видит 2025-02-01 00:00:00

    Документы, датированные концом месяца («31.01 23:59:59»), из-за этого
    попадают в соседний месяц. Ошибка тихая: исключения нет, просто цифры
    не те. Обнаружить удалось только сверкой двух независимых реализаций.

    Подставляются целые числа из datetime, инъекция невозможна.
    Где даты можно избежать вовсе — предпочитайте ГОД(Дата) и МЕСЯЦ(Дата)
    целыми параметрами.
    """
    return (f"ДАТАВРЕМЯ({moment.year},{moment.month},{moment.day},"
            f"{moment.hour},{moment.minute},{moment.second})")


def build_connection_string(
    base: str | None = None,
    srvr: str | None = None,
    ref: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> str:
    """Строка соединения для файловой или клиент-серверной базы.

    Пароль сюда передавать из аргументов командной строки нельзя — они видны
    в списке процессов. Штатный путь — переменная окружения ONEC_PWD.
    """
    if srvr:
        if not ref:
            raise ValueError("для клиент-серверной базы нужен и srvr, и ref")
        parts = [f'Srvr="{srvr}";', f'Ref="{ref}";']
    else:
        if not base:
            raise ValueError("нужен путь к файловой базе (base) либо srvr+ref")
        parts = [f'File="{base}";']
    if user:
        parts.append(f'Usr="{user}";')
        if password:
            parts.append(f'Pwd="{password}";')
    return "".join(parts)


class Base:
    """Открытое соединение с базой 1С.

    В Python точечная нотация к COM-объектам 1С работает нормально:

        b.md.Справочники.Количество()

    Обвязок в духе InvokeMember, которые нужны в PowerShell, здесь не требуется —
    см. docs/com-traps.md, раздел про PowerShell.
    """

    def __init__(
        self,
        base: str | None = None,
        *,
        srvr: str | None = None,
        ref: str | None = None,
        user: str | None = None,
        password: str | None = None,
        check_bits: bool = True,
    ) -> None:
        if check_bits:
            check_bitness()
        if password is None and user:
            password = os.environ.get("ONEC_PWD")

        conn_string = build_connection_string(base, srvr, ref, user, password)
        try:
            connector = win32com.client.Dispatch("V83.COMConnector")
            self.c = connector.Connect(conn_string)
        except Exception as exc:
            raise ConnectError(
                f"не удалось подключиться: {exc}\n"
                "Проверьте регистрацию коннектора (от администратора):\n"
                r'  regsvr32 "C:\Program Files\1cv8\<версия>\bin\comcntr.dll"'
            ) from exc
        finally:
            conn_string = ""  # строку с паролем не держим

        self.description = base or f"{srvr}/{ref}"
        self.md = self.c.Метаданные

    # ------------------------------------------------------------ запросы
    def rows(self, text: str, **params: Any) -> Iterator[Any]:
        """Выполнить запрос и отдать выборку построчно.

        Поля читаются как атрибуты: row.Дата, row.СуммаДокумента.
        Выборка — курсор, поэтому потреблять её нужно по порядку.

        ВАЖНО: даты параметрами не передавайте, см. datetime_literal().
        """
        q = self.c.NewObject("Запрос")
        q.Текст = text
        for name, value in params.items():
            q.УстановитьПараметр(name, value)
        selection = q.Выполнить().Выбрать()
        while selection.Следующий():
            yield selection

    def one(self, text: str, **params: Any) -> Any | None:
        """Первая строка результата или None."""
        for row in self.rows(text, **params):
            return row
        return None

    def count(self, full_name: str) -> int:
        """Число записей в таблице. -1 — таблица запросом не читается."""
        try:
            row = self.one(f"ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК N ИЗ {full_name}")
            return int(row.N) if row else 0
        except Exception:
            return -1

    # ---------------------------------------------------------- метаданные
    def names(self, collection: Any) -> list[str]:
        """Имена объектов коллекции метаданных."""
        return [collection.Получить(i).Имя for i in range(collection.Количество())]

    def find(self, collection: Any, name: str) -> Any | None:
        """Объект метаданных по имени. Имена НЕ угадывайте.

        Правильный путь — снять список через names() и искать в нём. Догадка
        обходится дорого: например, перечисление статусов обмена с банками
        называется СтатусыОбменСБанками — без «а» после «Обмен».
        """
        return collection.Найти(name)

    def s(self, value: Any) -> str:
        """Привести значение 1С к строке средствами платформы.

        ВНИМАНИЕ НА ИМЯ МЕТОДА. У глобального контекста COM-соединения есть
        только английское String (dispid 200002); имени «Строка» не существует.
        То же с NewObject — «НовыйОбъект» нет. При этом Метаданные, Найти,
        Получить, Количество, Выполнить, Выбрать, УстановитьПараметр —
        русские и работают.

        Контекст двуязычен НЕ симметрично, и угадывать язык нельзя.
        Проверять — через GetIDsOfNames:

            c._oleobj_.GetIDsOfNames("String")   # -> 200002
            c._oleobj_.GetIDsOfNames("Строка")   # -> com_error

        Ловится только AttributeError: широкий except здесь однажды превратил
        «такого имени нет» в тихий мусор '<COMObject <unknown>>' и сломал
        проверку, которая на этой строке держалась.

        Для полей ЗАПРОСА предпочитайте ПРЕДСТАВЛЕНИЕ(...) или .Код прямо
        в тексте: работу делает 1С, и это на один COM-вызов меньше на строку.
        String() нужен там, куда запросом не дотянуться, — объекты метаданных.
        """
        if value is None:
            return ""
        if isinstance(value, (str, int, float)):
            return str(value)
        try:
            return str(self.c.String(value))
        except AttributeError:
            return "<нет метода String у контекста>"

    # ------------------------------------------------------------- прочее
    @property
    def config_name(self) -> str:
        return str(self.md.Имя)

    @property
    def config_synonym(self) -> str:
        return str(self.md.Синоним)

    @property
    def config_version(self) -> str:
        return str(self.md.Версия)

    def platform_version(self) -> str:
        """Версия платформы из FileVersion зарегистрированного comcntr.dll.

        У COM-соединения нет ИнформацияОСистеме — обращение к нему даёт
        DISP_E_UNKNOWNNAME. Поэтому версию берём из библиотеки, которая
        это соединение и обслуживает.
        """
        try:
            import winreg

            import win32api

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Classes\V83.COMConnector\CLSID") as key:
                clsid = winreg.QueryValueEx(key, "")[0]
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SOFTWARE\Classes\CLSID\{clsid}\InprocServer32") as key:
                dll = winreg.QueryValueEx(key, "")[0]
            info = win32api.GetFileVersionInfo(dll, "\\")
            ms, ls = info["FileVersionMS"], info["FileVersionLS"]
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception:
            return "(не определена)"

    def close(self) -> None:
        self.c = None

    def __enter__(self) -> "Base":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
