"""Подменная база для тестов: к 1С не подключается."""
from __future__ import annotations

import types

import pytest

# Статусы, которые подменное перечисление отдает по умолчанию.
DEFAULT_STATUSES = [
    "ОтклоненБанком", "НеПодтвержден", "ПодготовленКОтправке", "ОшибкаПередачи",
    "Сформирован", "Отправлен", "НеСформирован", "Приостановлен",
]


class _Values:
    def __init__(self, names):
        self._names = list(names)

    def Количество(self):  # noqa: N802
        return len(self._names)

    def Получить(self, i):  # noqa: N802
        return types.SimpleNamespace(Имя=self._names[i])


class _Enums:
    def __init__(self, enums):
        self._enums = enums  # имя -> список имен значений

    def Найти(self, name):  # noqa: N802
        if name not in self._enums:
            return None
        return types.SimpleNamespace(
            Имя=name, ЗначенияПеречисления=_Values(self._enums[name])
        )


PLAUSIBLE_DOCS = [
    "РеализацияТоваровУслуг", "СчетНаОплатуПокупателю",
    "РегламентнаяОперация", "СообщениеОбменСБанками",
]


class _Coll(list):
    """Пустая коллекция метаданных с именем вида (Документы, Справочники)."""

    def __init__(self, kind):
        super().__init__()
        self.kind = kind


class _Md:
    """Метаданные: Перечисления работают по контракту, остальное - пустые коллекции."""

    def __init__(self, enums):
        self.Перечисления = _Enums(enums)

    def __getattr__(self, name):
        return _Coll(name)


class FakeBase:
    """rules: список (подстрока | предикат(text, params), результат | исключение).

    Первое совпавшее правило побеждает. Для rows результат - список объектов,
    для one - объект. Исключение (экземпляр BaseException) бросается.
    """

    def __init__(self):
        self.rules: list[tuple] = []
        self.raise_all = False
        self.error = RuntimeError("fake query failure")
        self.enums = {"СтатусыОбменСБанками": list(DEFAULT_STATUSES)}
        self.counts: dict[str, int] = {}
        self.description = "fake"
        self.config_synonym_value = "Тестовая конфигурация"
        self.config_version_value = "1.0.0.1"
        self.platform_value = "8.3.0.0"
        self.raise_on: set[str] = set()  # имена шапки: config_synonym, ...
        self.queries: list[str] = []
        self.fill_names = False

    # --- настройка ---
    def on(self, match, result):
        self.rules.append((match, result))
        return self

    def raise_when(self, match, exc=None):
        return self.on(match, exc or self.error)

    @staticmethod
    def _hit(match, text, params):
        if callable(match):
            return match(text, params)
        return match in text

    def _lookup(self, text, params, default):
        self.queries.append(text)
        if self.raise_all:
            raise self.error
        for match, result in self.rules:
            if self._hit(match, text, params):
                if isinstance(result, BaseException):
                    raise result
                return result
        return default

    # --- контракт Base ---
    def rows(self, text, **params):
        return iter(self._lookup(text, params, []))

    def one(self, text, **params):
        return self._lookup(text, params, None)

    def s(self, value):
        return "" if value is None else str(value)

    def count(self, full_name):
        if self.raise_all:
            raise self.error
        return self.counts.get(full_name, 0)

    @property
    def md(self):
        return _Md(self.enums)

    # Вспомогательные методы Base (в контракте спеки не перечислены).
    def names(self, collection):
        # По умолчанию метаданные пусты. В режиме "бросать на всё" или при
        # fill_names=True отдаются правдоподобные имена (У7), чтобы проверки
        # дошли до запросов.
        if not (self.raise_all or self.fill_names):
            return []
        kind = getattr(collection, "kind", "")
        if kind == "Документы":
            return list(PLAUSIBLE_DOCS)
        if kind == "Справочники":
            return ["Контрагенты"]
        return []

    def find(self, collection, name):
        return None

    def _head(self, name, value):
        if name in self.raise_on:
            raise RuntimeError(f"fake header failure: {name}")
        return value

    @property
    def config_synonym(self):
        return self._head("config_synonym", self.config_synonym_value)

    @property
    def config_version(self):
        return self._head("config_version", self.config_version_value)

    @property
    def config_name(self):
        return self._head("config_name", "Тест")

    def platform_version(self):
        return self._head("platform_version", self.platform_value)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None


@pytest.fixture
def fake_base():
    return FakeBase()


@pytest.fixture
def patch_base(monkeypatch):
    """patch_base(fake) подменяет onec_audit.cli.Base фабрикой, отдающей fake."""
    def _patch(fake):
        monkeypatch.setattr("onec_audit.cli.Base", lambda *a, **k: fake)
        return fake
    return _patch
