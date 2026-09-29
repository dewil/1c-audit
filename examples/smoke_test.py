# -*- coding: utf-8 -*-
"""Проверка, что COM-доступ к 1С из Python работает и грабли не воспроизводятся.

Бьёт ровно в те места, где ломается PowerShell (docs/com-traps.md, грабли 1-3),
и заодно показывает, как правильно обходить две питоновские (грабли 4-5).

Запуск:
    set ONEC_BASE=C:\\path\\to\\base
    python examples/smoke_test.py

    python examples/smoke_test.py "C:\\path\\to\\base"
"""
from __future__ import annotations

import datetime as dt
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from onec_audit import Base, datetime_literal  # noqa: E402


def main() -> int:
    base_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ONEC_BASE")
    if not base_path:
        print("Укажите базу: аргументом или переменной ONEC_BASE")
        return 2

    print(f"Python {sys.version.split()[0]}, {struct.calcsize('P') * 8}-bit")
    print(f"База: {base_path}\n")

    with Base(base_path) as b:
        print("Connected.\n")

        # --- грабля 1: чтение свойства точечной нотацией -----------------
        # В PowerShell $c.Метаданные молча отдаёт $null, и всё приходится
        # гнать через InvokeMember. В Python работает как есть.
        print(f"1  b.md                     -> {type(b.md).__name__}")
        print(f"   b.md.Имя                 -> {b.config_name}")
        print(f"   b.md.Синоним             -> {b.config_synonym}")

        # --- грабля 2: коллекция, возвращённая из функции ----------------
        # В PowerShell конвейер разворачивает её в Object[], и Количество()
        # теряется. В Python возврат из функции коллекцию не портит.
        def get_catalogs(metadata):
            return metadata.Справочники

        catalogs = get_catalogs(b.md)
        print(f"\n2  коллекция из функции     -> {type(catalogs).__name__}")
        print(f"   Количество()             -> {catalogs.Количество()}")
        print(f"   Документы.Количество()   -> {b.md.Документы.Количество()}")

        # --- грабля 3: строка после обработки списка ---------------------
        # В PowerShell Where-Object оборачивает строку в PSObject, COM
        # получает обёртку вместо BSTR, и Найти() молча не находит объект.
        docs = b.md.Документы
        names = [docs.Получить(i).Имя for i in range(docs.Количество())]
        target = next((n for n in names if n.startswith("Реализация")), names[0])
        print(f"\n3  имя из обработки списка  -> {target!r} ({type(target).__name__})")
        found = b.find(docs, target)
        print(f"   Найти(это имя)           -> "
              f"{'НАЙДЕН: ' + str(found.Синоним) if found else 'None'}")

        # --- грабля 5: приведение к строке ------------------------------
        # Имени «Строка» у контекста нет, есть только String. Base.s()
        # это учитывает.
        attr = found.Реквизиты.Получить(0) if found.Реквизиты.Количество() else None
        if attr is not None:
            print(f"\n5  тип реквизита {attr.Имя!r}")
            print(f"   через Base.s()           -> {b.s(attr.Тип)!r}")

        # --- грабля 4: дата в запросе -----------------------------------
        # Параметром дата сдвинулась бы на часовой пояс. Литерал — нет.
        year = dt.date.today().year
        row = b.one(f"""
            ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК N
            ИЗ Документ.{target}
            ГДЕ Проведен И НЕ ПометкаУдаления
              И Дата >= {datetime_literal(dt.datetime(year, 1, 1))}
        """)
        print(f"\n4  {target} за {year}: {int(row.N) if row else 0} шт")
        print("   (границу периода задали литералом ДАТАВРЕМЯ, не параметром)")

        print("\nГотово. В базу не записано ничего.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
