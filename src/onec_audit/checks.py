# -*- coding: utf-8 -*-
"""Проверки учётной базы. Только чтение.

Принципы, по которым они устроены, — docs/check-design.md. Коротко:
находка попадает в сводку, только если человек может с ней что-то сделать,
а признак должен быть проверяемым, а не косвенным.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .base import Base, datetime_literal
from .report import Report, money


@dataclass
class Options:
    """Настройки прогона."""

    since_year: int = field(default_factory=lambda: dt.date.today().year - 2)
    """С какого года смотреть обмен с банком."""

    locked_before: dt.date | None = None
    """Граница закрытого периода: всё строго раньше неё закрыто.

    Находки раньше этой границы печатаются с пометкой, но в сводку не идут:
    исправить их всё равно нельзя, а сводку они забивают. None — границы
    нет, вся история изменяема. Сравнивать только через is_locked().
    """

    locked_source: str = "не задана"
    """Откуда граница: «параметр», «дата запрета в базе» или «не задана»."""


# Служебные типы: не проводятся по устройству. Только явный перечень, эвристик
# «непроведены все» и порогов по количеству нет: такая эвристика спрятала
# непроведённые регламентные операции, то есть главную находку прогона.
SERVICE_DOC_TYPES = frozenset({
    "ПакетОбменСБанками", "СообщениеОбменСБанками", "РегламентированныйОтчет",
})

# Непроведённые регламентные операции — находка раздела «Закрытие месяца»,
# в счётчик непроведённых сводки не входят. В служебные не попадают никогда.
NEVER_STRUCTURAL = {"РегламентнаяОперация"}


def is_locked(moment: dt.date | dt.datetime, opt: Options) -> bool:
    """Закрыт ли момент: строго раньше границы, сравнение по календарной дате."""
    if opt.locked_before is None:
        return False
    day = moment.date() if isinstance(moment, dt.datetime) else moment
    return day < opt.locked_before


def months_to_check(opt: Options, today: dt.date) -> list[tuple[int, int]]:
    """Месяцы для закрытия: с месяца границы (без границы — с января прошлого
    года) по последний завершённый включительно."""
    start = opt.locked_before or dt.date(today.year - 1, 1, 1)
    year, month = start.year, start.month
    result = []
    while (year, month) < (today.year, today.month):
        result.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def resolve_boundary(b: Base, r: Report, opt: Options) -> None:
    """Граница из базы, если параметром она не задана.

    Берётся только общая дата запрета «для всех пользователей» при включённой
    константе. Дата запрета в 1С — последний закрытый день, граница — следующий.
    Отсутствие регистра, константы, записи или пустая дата — не отказ.
    """
    if opt.locked_before is not None:
        return
    opt.locked_source = "не задана"
    try:
        if ("ИспользоватьДатыЗапретаИзменения" not in b.names(b.md.Константы)
                or "ДатыЗапретаИзменения" not in b.names(b.md.РегистрыСведений)):
            return
        used = b.one("ВЫБРАТЬ Значение КАК Значение "
                     "ИЗ Константа.ИспользоватьДатыЗапретаИзменения")
        if not used or not used.Значение:
            return
        # Имена не угадываем: нет перечисления или значения — границы нет.
        enum = b.md.Перечисления.Найти("ВидыНазначенияДатЗапрета")
        if enum is None:
            return
        values = enum.ЗначенияПеречисления
        if "ДляВсехПользователей" not in {values.Получить(i).Имя
                                          for i in range(values.Количество())}:
            return
        # Объект — составного типа: общая запись у него пустая ссылка или NULL.
        row = b.one("""
            ВЫБРАТЬ МАКСИМУМ(ДатаЗапрета) КАК ДатаЗапрета
            ИЗ РегистрСведений.ДатыЗапретаИзменения
            ГДЕ Пользователь = ЗНАЧЕНИЕ(Перечисление.ВидыНазначенияДатЗапрета.ДляВсехПользователей)
              И Раздел = ЗНАЧЕНИЕ(ПланВидовХарактеристик.РазделыДатЗапретаИзменения.ПустаяСсылка)
              И (Объект ЕСТЬ NULL
                 ИЛИ Объект = ЗНАЧЕНИЕ(ПланВидовХарактеристик.РазделыДатЗапретаИзменения.ПустаяСсылка))
        """)
        value = row.ДатаЗапрета if row else None
        if value is None:
            return
        day = value.date() if isinstance(value, dt.datetime) else value
        if day.year <= 1:  # пустая дата 1С
            return
        opt.locked_before = day + dt.timedelta(days=1)
        opt.locked_source = "дата запрета в базе"
    except Exception as exc:
        r.fail("locked_boundary", str(exc))


# ---------------------------------------------------------------------------
def _open_cond(opt: Options) -> str:
    """Условие «открытый период» для текста запроса; границы нет — пустая строка.

    Граница — литералом, не параметром: см. datetime_literal().
    """
    if opt.locked_before is None:
        return ""
    floor = dt.datetime.combine(opt.locked_before, dt.time())
    return f" И Дата >= {datetime_literal(floor)}"


def unposted_documents(b: Base, r: Report, opt: Options) -> None:
    """Непроведённые документы.

    Непроведённый документ не делает проводок и на учёт не влияет. Вопрос
    не в учёте, а в том, состоялась ли операция и получил ли контрагент
    первичный документ.
    """
    r.head("1. НЕПРОВЕДЁННЫЕ ДОКУМЕНТЫ")
    if opt.locked_before is not None:
        r.w(f"Период до {opt.locked_before:%d.%m.%Y} закрыт: правки там невозможны.")
        r.w("Такие строки помечены [закрыт] и в сводку не попадают.")
    r.w()

    scan = b.names(b.md.Документы)

    open_cond = _open_cond(opt)
    rows = []
    failed = False
    for name in scan:
        try:
            row = b.one(
                f"""
                ВЫБРАТЬ
                    КОЛИЧЕСТВО(*) КАК Всего,
                    СУММА(ВЫБОР КОГДА НЕ Проведен ТОГДА 1 ИНАЧЕ 0 КОНЕЦ) КАК Непров,
                    СУММА(ВЫБОР КОГДА НЕ Проведен{open_cond} ТОГДА 1 ИНАЧЕ 0 КОНЕЦ) КАК Открыт
                ИЗ Документ.{name}
                ГДЕ НЕ ПометкаУдаления
                """
            )
        except Exception as exc:
            r.fail(unposted_documents.__name__, f"{name}: {exc}")
            failed = True
            continue
        if not row or not row.Непров:
            continue
        rows.append({"тип": name, "всего": int(row.Всего),
                     "непров": int(row.Непров), "открыт": int(row.Открыт or 0)})

    service = [x for x in rows if x["тип"] in SERVICE_DOC_TYPES]
    regular = [x for x in rows if x["тип"] not in SERVICE_DOC_TYPES]

    if not regular:
        if not failed:
            r.w("   непроведённых документов нет")
    else:
        r.w(f"   {'Тип документа':<38}{'всего':>8}{'непров.':>10}{'в открытом':>12}")
        for x in sorted(regular, key=lambda v: -v["непров"]):
            r.w(f"   {x['тип']:<38}{x['всего']:>8}{x['непров']:>10}{x['открыт']:>12}")
        r.w()
        for x in regular:
            if x["непров"] > x["открыт"]:
                r.w(f"   {x['тип']}: в закрытом периоде {x['непров'] - x['открыт']}")

    if service:
        r.w()
        r.w("   Служебные типы — не проводятся по устройству, в сводку не идут:")
        for x in service:
            r.w(f"     {x['тип']:<38}непроведено {x['непров']} из {x['всего']}")

    for_summary = [x for x in regular if x["тип"] not in NEVER_STRUCTURAL]
    if len(for_summary) != len(regular):
        r.w()
        r.w("   Регламентные операции здесь не считаются — это закрытие месяца, см. раздел 6.")
    open_total = sum(x["открыт"] for x in for_summary)
    all_total = sum(x["непров"] for x in for_summary)
    if not open_total and all_total:
        r.w()
        r.w(f"   Все {all_total} непроведённых — в закрытом периоде, правки невозможны.")

    for x in for_summary:
        if x["открыт"]:
            _unposted_list(b, r, opt, x["тип"])
    if open_total:
        r.finding(f"непроведённых в открытом периоде: {open_total}")


def _unposted_list(b: Base, r: Report, opt: Options, name: str) -> None:
    """Непроведённые документы типа в открытом периоде: дата, номер, контрагент.

    Для продаж добавляется признак оплаты: есть ли проведённое поступление,
    которое ссылается на счёт. Суммы не сравниваются.
    """
    sales = name in ("РеализацияТоваровУслуг", "СчетНаОплатуПокупателю")
    try:
        # Есть ли у типа реквизит Контрагент, берём из метаданных, не угадываем.
        meta = b.find(b.md.Документы, name)
        has_partner = meta is not None and b.find(meta.Реквизиты, "Контрагент") is not None
        partner = "ПРЕДСТАВЛЕНИЕ(Контрагент)" if has_partner else '""'
        # У части типов номера нет (длина номера 0): поля Номер в запросе тоже нет.
        number = "Номер" if meta is None or int(meta.ДлинаНомера) > 0 else '""'
        extra = ""
        if name == "РеализацияТоваровУслуг":
            extra = (", ВЫБОР КОГДА СчетНаОплатуПокупателю = ЗНАЧЕНИЕ("
                     "Документ.СчетНаОплатуПокупателю.ПустаяСсылка) ТОГДА ЛОЖЬ ИНАЧЕ ИСТИНА КОНЕЦ"
                     " КАК ЕстьСчет, СчетНаОплатуПокупателю КАК Счет")
        elif name == "СчетНаОплатуПокупателю":
            extra = ", Ссылка"
        # Строка выборки — курсор: поля снимаем сразу, иначе все строки станут последней.
        items = [
            {"д": row.Дата, "н": b.s(row.Номер) or "-",
             "к": b.s(getattr(row, "Контр", None)) or "-",
             "ссылка": (row.Счет if name == "РеализацияТоваровУслуг" else row.Ссылка) if sales else None,
             "нет_счёта": name == "РеализацияТоваровУслуг" and not row.ЕстьСчет}
            for row in b.rows(f"""// непроведённые: {name}
                ВЫБРАТЬ Дата, {number} КАК Номер, {partner} КАК Контр{extra}
                ИЗ Документ.{name}
                ГДЕ НЕ Проведен И НЕ ПометкаУдаления{_open_cond(opt)}
                УПОРЯДОЧИТЬ ПО Дата УБЫВ
            """)
        ]
    except Exception as exc:
        r.fail(unposted_documents.__name__, f"список {name}: {exc}")
        return

    r.w()
    r.w(f"   {name}: непроведённых в открытом периоде {len(items)}")
    r.w("   " + "." * 96)
    for it in items:
        line = f"   {it['д']:%Y-%m-%d}  {it['н']:<15}{it['к'][:29]:<30}"
        if sales:
            if it["нет_счёта"]:
                paid = "нет счёта"
            else:
                try:
                    hit = b.one(
                        """// оплата счёта
                        ВЫБРАТЬ ПЕРВЫЕ 1 Р.Ссылка.Дата КАК Дата
                        ИЗ Документ.ПоступлениеНаРасчетныйСчет.РасшифровкаПлатежа КАК Р
                        ГДЕ Р.СчетНаОплату = &Счет
                          И Р.Ссылка.Проведен И НЕ Р.Ссылка.ПометкаУдаления
                        УПОРЯДОЧИТЬ ПО Р.Ссылка.Дата
                        """,
                        Счет=it["ссылка"],
                    )
                    paid = f"ДА {hit.Дата:%Y-%m-%d}" if hit else "НЕТ"
                except Exception as exc:
                    r.fail(unposted_documents.__name__, f"поиск оплаты {name}: {exc}")
                    paid = "?"
            line += f"оплата {paid}"
        r.w(line.rstrip())

    if sales:
        # Оговорка печатается рядом с находкой, а не сноской в конце отчёта.
        r.w("   ОГОВОРКА: «ДА» значит «есть хотя бы одна проведённая оплата по ссылке")
        r.w("   на счёт», а не «оплачено полностью»: суммы не сравниваются.")


# ---------------------------------------------------------------------------
BAD_STATUSES = [
    "ОтклоненБанком", "НеПодтвержден", "ПодготовленКОтправке", "ОшибкаПередачи",
    "Сформирован", "Отправлен", "НеСформирован", "Приостановлен",
]


def bank_exchange(b: Base, r: Report, opt: Options) -> None:
    """Сообщения обмена с банком в незавершённых статусах.

    Статус сообщения — СЛАБЫЙ признак: он не доказывает, что деньги не ушли.
    Сильный признак — сообщение-сирота: ссылка на номер платёжки, которой
    в базе нет. Так выглядит платёжка, ушедшая в банк, а потом удалённая
    и пересозданная под следующим номером. Видно по дыркам в нумерации.
    """
    r.head(f"2. ОБМЕН С БАНКОМ: НЕЗАВЕРШЁННЫЕ СТАТУСЫ (с {opt.since_year} года)")
    doc_names = b.names(b.md.Документы)
    if "СообщениеОбменСБанками" not in doc_names:
        r.w("   обмен с банками в этой конфигурации не используется")
        return

    # Статусы берём из метаданных, не угадываем: см. Base.find().
    enum = b.md.Перечисления.Найти("СтатусыОбменСБанками")
    if enum is None:
        r.fail(bank_exchange.__name__, "в метаданных нет перечисления СтатусыОбменСБанками")
        return
    values = enum.ЗначенияПеречисления
    known = {values.Получить(i).Имя for i in range(values.Количество())}

    pp_keys: dict[tuple[int, int], str] = {}
    if "ПлатежноеПоручение" in doc_names:
        for row in b.rows(
            "ВЫБРАТЬ Дата, Номер ИЗ Документ.ПлатежноеПоручение ГДЕ ГОД(Дата) >= &Год",
            Год=opt.since_year,
        ):
            raw = b.s(row.Номер)
            tail = raw.split("-")[-1]
            if tail.isdigit():
                pp_keys[(row.Дата.year, int(tail))] = raw

    orphans, any_live, failed = [], False, False
    for status in BAD_STATUSES:
        if status not in known:
            r.w(f"   {status}: пропущен — такого значения нет в перечислении")
            continue
        try:
            # Сравнение с ЗНАЧЕНИЕ() и текст через ПРЕДСТАВЛЕНИЕ() делает 1С:
            # перечисление приезжает в Python объектом, а не строкой.
            msgs = [
                {"д": row.Дата, "сум": float(row.СуммаДокумента or 0),
                 "вид": b.s(row.ВидТекст), "платежка": bool(row.ЭтоПлатежка),
                 "ном": b.s(row.НомерДокументаОтправителя).strip(),
                 "контр": b.s(row.НаименованиеКонтрагента)}
                for row in b.rows(f"""
                    ВЫБРАТЬ Дата, СуммаДокумента,
                           ПРЕДСТАВЛЕНИЕ(ВидЭД) КАК ВидТекст,
                           ВЫБОР КОГДА ВидЭД = ЗНАЧЕНИЕ(Перечисление.ВидыЭДОбменСБанками.ПлатежноеПоручение)
                                 ТОГДА ИСТИНА ИНАЧЕ ЛОЖЬ КОНЕЦ КАК ЭтоПлатежка,
                           НомерДокументаОтправителя, НаименованиеКонтрагента
                    ИЗ Документ.СообщениеОбменСБанками
                    ГДЕ НЕ ПометкаУдаления И ГОД(Дата) >= &Год
                      И Статус = ЗНАЧЕНИЕ(Перечисление.СтатусыОбменСБанками.{status})
                    УПОРЯДОЧИТЬ ПО Дата УБЫВ
                """, Год=opt.since_year)
            ]
        except Exception as exc:
            r.fail(bank_exchange.__name__, f"{status}: {exc}")
            failed = True
            continue
        if not msgs:
            continue

        live = []
        for m in msgs:
            is_orphan = (m["платежка"] and m["ном"].isdigit() and pp_keys
                         and (m["д"].year, int(m["ном"])) not in pp_keys)
            (orphans if is_orphan else live).append(
                {**m, "статус": status} if is_orphan else m)
        if not live:
            continue

        any_live = True
        total = sum(m["сум"] for m in live)
        r.w(f"   == {status}: {len(live)} шт, сумма {money(total)}")
        for m in live[:10]:
            r.w(f"      {m['д']:%Y-%m-%d} | {m['вид'][:26]:<26} | {money(m['сум']):>13} "
                f"| N {m['ном']} {m['контр']}")
        if total:
            r.finding(f"обмен с банком, статус {status}: {len(live)} шт на {money(total)}")

    if not any_live and not failed:
        r.w("   незавершённых сообщений, требующих внимания, нет")

    if orphans:
        r.w()
        r.w(f"   Сообщения-СИРОТЫ — {len(orphans)} шт: ссылаются на номера платёжек,")
        r.w("   которых в базе нет. Платёжка ушла в банк, потом была удалена")
        r.w("   и пересоздана под следующим номером — деньги ушли по новому")
        r.w("   документу. Это мусор в журнале обмена, а не проблема учёта.")
        for m in sorted(orphans, key=lambda v: v["д"], reverse=True):
            r.w(f"      {m['д']:%Y-%m-%d} | N {m['ном']:<4} | {money(m['сум']):>13} | {m['статус']}")

    r.w()
    r.w("   ОГОВОРКА: статус сообщения не доказывает, что деньги не ушли.")
    r.w("   Настоящий признак — отсутствие проведённого списания с расчётного счёта.")


# ---------------------------------------------------------------------------
def partners(b: Base, r: Report, opt: Options) -> None:
    """Качество справочника контрагентов.

    Дубль — совпадение ИНН И КПП. Один ИНН при разных КПП это филиалы одной
    организации, и это законно: проверка по одному ИНН даёт ложные
    срабатывания на каждой компании с обособленными подразделениями.
    """
    r.head("3. КОНТРАГЕНТЫ")
    try:
        groups: dict[str, list[dict]] = {}
        for row in b.rows("""
            ВЫБРАТЬ К.ИНН КАК ИНН, К.КПП КАК КПП, К.Код КАК Код, К.Наименование КАК Наим
            ИЗ Справочник.Контрагенты КАК К
            ГДЕ НЕ К.ПометкаУдаления И К.ИНН В (
                ВЫБРАТЬ К2.ИНН ИЗ Справочник.Контрагенты КАК К2
                ГДЕ К2.ИНН <> "" И НЕ К2.ПометкаУдаления
                СГРУППИРОВАТЬ ПО К2.ИНН
                ИМЕЮЩИЕ КОЛИЧЕСТВО(*) > 1)
            УПОРЯДОЧИТЬ ПО ИНН, Код
        """):
            groups.setdefault(b.s(row.ИНН), []).append(
                {"кпп": b.s(row.КПП), "код": b.s(row.Код), "наим": b.s(row.Наим)})

        real = {inn: rs for inn, rs in groups.items() if len({x["кпп"] for x in rs}) == 1}
        branches = {inn: rs for inn, rs in groups.items() if inn not in real}

        if not real:
            r.w("   настоящих дублей нет (пар с одинаковыми ИНН И КПП не найдено)")
        else:
            r.w("   ДУБЛИ — совпадают ИНН И КПП:")
            for inn, rs in real.items():
                r.w(f"     ИНН {inn}  КПП {rs[0]['кпп']}")
                for x in rs:
                    r.w(f"        код {x['код']:<12} {x['наим']}")
            r.finding(f"настоящих дублей контрагентов: {len(real)} групп")

        if branches:
            r.w()
            r.w(f"   Не дубли — филиалы (один ИНН, разные КПП): {len(branches)} групп")
            for inn, rs in branches.items():
                r.w(f"     ИНН {inn}")
                for x in rs:
                    r.w(f"        КПП {x['кпп']:<12} код {x['код']:<12} {x['наим']}")
    except Exception as exc:
        r.fail(partners.__name__, f"дубли: {exc}")

    try:
        # Имена не угадываем: нет перечисления или значения — вид не определён.
        physical = "ЛОЖЬ"
        enum = b.md.Перечисления.Найти("ЮридическоеФизическоеЛицо")
        if enum is not None and "ФизическоеЛицо" in {
                enum.ЗначенияПеречисления.Получить(i).Имя
                for i in range(enum.ЗначенияПеречисления.Количество())}:
            physical = ("ВЫБОР КОГДА К.ЮридическоеФизическоеЛицо = ЗНАЧЕНИЕ("
                        "Перечисление.ЮридическоеФизическоеЛицо.ФизическоеЛицо) "
                        "ТОГДА ИСТИНА ИНАЧЕ ЛОЖЬ КОНЕЦ")
        else:
            r.w("   вид контрагента не определён: в метаданных нет "
                "ЮридическоеФизическоеЛицо.ФизическоеЛицо, все считаются юрлицами")

        # Документы в открытом периоде: типы с реквизитом Контрагент — из метаданных.
        docs = b.md.Документы
        types = [name for name in b.names(docs)
                 if (meta := b.find(docs, name)) is not None
                 and b.find(meta.Реквизиты, "Контрагент") is not None]
        batch, join, has_docs = "", "", "ЛОЖЬ"
        if types:
            cond = _open_cond(opt)
            batch = " ОБЪЕДИНИТЬ ВСЕ ".join(
                f"ВЫБРАТЬ Контрагент КАК Контр {'ПОМЕСТИТЬ Движ ' if i == 0 else ''}"
                f"ИЗ Документ.{name} ГДЕ НЕ ПометкаУдаления{cond}"
                for i, name in enumerate(types)) + ";\n"
            join = "ЛЕВОЕ СОЕДИНЕНИЕ Движ КАК Д ПО Д.Контр = К.Ссылка"
            has_docs = "ВЫБОР КОГДА Д.Контр ЕСТЬ NULL ТОГДА ЛОЖЬ ИНАЧЕ ИСТИНА КОНЕЦ"
        items = [
            {"код": b.s(row.Код), "наим": b.s(row.Наим),
             "физ": bool(row.Физ),
             # «Есть» — зарезервированное слово языка запросов, поэтому ЕстьДокументы.
             "есть": bool(row.ЕстьДокументы)}
            for row in b.rows(f"""// список: без ИНН
            {batch}ВЫБРАТЬ РАЗЛИЧНЫЕ К.Код КАК Код, К.Наименование КАК Наим,
                   {physical} КАК Физ, {has_docs} КАК ЕстьДокументы
            ИЗ Справочник.Контрагенты КАК К {join}
            ГДЕ К.ИНН = "" И НЕ К.ЭтоГруппа И НЕ К.ПометкаУдаления
            УПОРЯДОЧИТЬ ПО Код
            """)
        ]
        r.w(f"   без ИНН: {len(items)}")
        for x in items:
            r.w(f"      код {x['код']:<12}{x['наим'][:40]:<42}"
                f"{'физлицо' if x['физ'] else 'юрлицо':<9}"
                f"документы в открытом периоде: {'да' if x['есть'] else 'нет'}")
        # В сводку — только юрлица с документами: у физлица ИНН часто не нужен,
        # без движений карточка ни на что не влияет.
        named = [x["наим"] for x in items if x["есть"] and not x["физ"]]
        if named:
            r.finding(f"юрлиц без ИНН с документами: {len(named)} ({', '.join(named)})")
    except Exception as exc:
        r.fail(partners.__name__, f"без ИНН: {exc}")


# ---------------------------------------------------------------------------
def balances(b: Base, r: Report, opt: Options) -> None:
    """Остатки по счетам бухгалтерского учёта."""
    r.head("4. ОСТАТКИ ПО СЧЕТАМ")
    try:
        seen = False
        for row in b.rows("""
            ВЫБРАТЬ Ост.Счет.Код КАК Код, СУММА(Ост.СуммаОстатокДт) КАК Дт,
                   СУММА(Ост.СуммаОстатокКт) КАК Кт
            ИЗ РегистрБухгалтерии.Хозрасчетный.Остатки(, , , ) КАК Ост
            СГРУППИРОВАТЬ ПО Ост.Счет
            УПОРЯДОЧИТЬ ПО Код
        """):
            debit, credit = float(row.Дт or 0), float(row.Кт or 0)
            if debit or credit:
                seen = True
                r.w(f"   {b.s(row.Код):<34} Дт {money(debit):>15}   Кт {money(credit):>15}")
        if not seen:
            r.w("   остатков нет")
    except Exception as exc:
        r.fail(balances.__name__, str(exc))


# ---------------------------------------------------------------------------
def marked_for_deletion(b: Base, r: Report, opt: Options) -> None:
    """Объекты, помеченные на удаление."""
    r.head("5. ПОМЕЧЕННЫЕ НА УДАЛЕНИЕ")
    marked, failed = [], False
    for coll, prefix in ((b.md.Справочники, "Справочник"), (b.md.Документы, "Документ")):
        for name in b.names(coll):
            try:
                row = b.one(f"ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК N ИЗ {prefix}.{name} ГДЕ ПометкаУдаления")
                if row and int(row.N):
                    marked.append((f"{prefix}.{name}", int(row.N)))
            except Exception as exc:
                r.fail(marked_for_deletion.__name__, f"{prefix}.{name}: {exc}")
                failed = True
    if not marked:
        if not failed:
            r.w("   помеченных нет")
        return
    for obj, n in sorted(marked, key=lambda v: -v[1]):
        r.w(f"   {obj:<56}{n:>6}")
    r.w(f"   итого: {sum(n for _, n in marked)} — убирается штатным "
        "«Удаление помеченных объектов»")


# ---------------------------------------------------------------------------
def month_closing(b: Base, r: Report, opt: Options) -> None:
    """Закрытие месяца по завершённым месяцам.

    Прямого признака «месяц закрыт» в базе нет, поэтому смотрим косвенно:
    есть ли регламентные операции за месяц, проведены ли они, и не висят ли
    несписанные затраты на конец месяца. Текущий месяц не проверяем —
    его закрывать ещё рано.
    """
    r.head("6. ЗАКРЫТИЕ МЕСЯЦА")
    if "РегламентнаяОперация" not in b.names(b.md.Документы):
        r.w("   документа РегламентнаяОперация нет")
        return

    months = months_to_check(opt, dt.date.today())
    if not months:
        r.w("   завершённых месяцев в открытом периоде нет")
        return
    first, last = dt.datetime(*months[0], 1), dt.datetime(*months[-1], 1)

    r.w(f"   Проверяются завершённые месяцы: {first:%Y-%m} .. {last:%Y-%m}")
    r.w()
    r.w(f"   {'Месяц':<10}{'операций':>9}{'проведено':>11}   Состояние")

    unclosed = []
    for year, num in months:
        month = dt.datetime(year, num, 1)
        end = (month + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(seconds=1)
        total = posted = 0
        failed = False
        try:
            # ГОД/МЕСЯЦ целыми: дат в параметрах нет, значит нет и сдвига пояса.
            row = b.one("""
                ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК N,
                       СУММА(ВЫБОР КОГДА Проведен ТОГДА 1 ИНАЧЕ 0 КОНЕЦ) КАК P
                ИЗ Документ.РегламентнаяОперация
                ГДЕ НЕ ПометкаУдаления И ГОД(Дата) = &Г И МЕСЯЦ(Дата) = &М
            """, Г=month.year, М=month.month)
            if row:
                total, posted = int(row.N or 0), int(row.P or 0)
        except Exception as exc:
            r.fail(month_closing.__name__, f"{month:%Y-%m}: {exc}")
            failed = True

        residue, residue_failed = [], False
        try:
            for row in b.rows(f"""
                ВЫБРАТЬ Ост.Счет.Код КАК Код, Ост.СуммаОстатокДт КАК Дт
                ИЗ РегистрБухгалтерии.Хозрасчетный.Остатки({datetime_literal(end)}, , , ) КАК Ост
                ГДЕ Ост.СуммаОстатокДт <> 0
            """):
                acc = b.s(row.Код)
                if acc.startswith(("20", "25", "26", "44", "90.09")):
                    residue.append(f"{acc} {money(row.Дт)}")
        except Exception as exc:
            r.fail(month_closing.__name__, f"остатки {month:%Y-%m}: {exc}")
            residue_failed = True

        if failed:
            state = "НЕ ПРОВЕРЕН — запрос не выполнен"
        elif total == 0:
            state = "НЕ ЗАКРЫВАЛСЯ — операций нет"
        elif posted == 0:
            state = "НЕ ЗАКРЫТ — операции есть, ни одна не проведена"
        elif posted < total:
            state = f"ЧАСТИЧНО — проведено {posted} из {total}"
        else:
            state = "закрыт"
        if residue and state != "закрыт":
            state += "  [" + "; ".join(residue) + "]"

        bad = state != "закрыт" and not failed
        if residue_failed:
            state = "остатки не проверены" if state == "закрыт" else state + "  [остатки не проверены]"
        r.w(f"   {month:%Y-%m}   {total:>9}{posted:>11}   {state}")
        if bad:
            unclosed.append(month)

    if unclosed:
        r.finding(f"не закрыто месяцев: {len(unclosed)} "
                  f"(с {unclosed[0]:%Y-%m} по {unclosed[-1]:%Y-%m})")
        r.w()
        r.w("   Закрывается: Операции → Закрытие месяца.")
        r.w("   Помнить: правка документа задним числом снимает закрытие того")
        r.w("   месяца и всех следующих — их надо перезакрыть.")


# ---------------------------------------------------------------------------
_EMPTY_INVOICE = "ЗНАЧЕНИЕ(Документ.СчетНаОплатуПокупателю.ПустаяСсылка)"

# (название списка, текст в сводке, псевдоним документа, запрос). Связь — только
# из реквизитов. «<период>» — место условия по границе закрытого периода.
_LINK_LISTS = [
    ("Акты без счёта", "акты без счёта", "Д", f"""// список: акты без счёта
        ВЫБРАТЬ Д.Дата КАК Дата, Д.Номер КАК Номер,
               ПРЕДСТАВЛЕНИЕ(Д.Контрагент) КАК Контр, Д.Контрагент.ИНН КАК ИНН
        ИЗ Документ.РеализацияТоваровУслуг КАК Д
        ГДЕ Д.Проведен И НЕ Д.ПометкаУдаления
          И Д.СчетНаОплатуПокупателю = {_EMPTY_INVOICE}<период>
    """),
    ("Счета без акта и оплаты", "счета без акта и оплаты", "С", """// список: счета без акта и оплаты
        ВЫБРАТЬ С.Дата КАК Дата, С.Номер КАК Номер,
               ПРЕДСТАВЛЕНИЕ(С.Контрагент) КАК Контр, С.Контрагент.ИНН КАК ИНН
        ИЗ Документ.СчетНаОплатуПокупателю КАК С
        ГДЕ С.Проведен И НЕ С.ПометкаУдаления
          И НЕ С.Ссылка В (
              ВЫБРАТЬ Р.СчетНаОплатуПокупателю
              ИЗ Документ.РеализацияТоваровУслуг КАК Р
              ГДЕ Р.Проведен И НЕ Р.ПометкаУдаления)
          И НЕ С.Ссылка В (
              ВЫБРАТЬ РП.СчетНаОплату
              ИЗ Документ.ПоступлениеНаРасчетныйСчет.РасшифровкаПлатежа КАК РП
              ГДЕ РП.Ссылка.Проведен И НЕ РП.Ссылка.ПометкаУдаления)<период>
    """),
    ("Поступления без счёта", "поступления без счёта", "П", f"""// список: поступления без счёта
        ВЫБРАТЬ П.Дата КАК Дата, П.Номер КАК Номер,
               ПРЕДСТАВЛЕНИЕ(П.Контрагент) КАК Контр, П.Контрагент.ИНН КАК ИНН,
               ПРЕДСТАВЛЕНИЕ(П.ВидОперации) КАК Вид
        ИЗ Документ.ПоступлениеНаРасчетныйСчет КАК П
        ГДЕ П.Проведен И НЕ П.ПометкаУдаления
          И НЕ П.Ссылка В (
              ВЫБРАТЬ РП.Ссылка
              ИЗ Документ.ПоступлениеНаРасчетныйСчет.РасшифровкаПлатежа КАК РП
              ГДЕ РП.СчетНаОплату <> {_EMPTY_INVOICE})<период>
    """),
]


def document_links(b: Base, r: Report, opt: Options) -> None:
    """Связи документов продажи: акт — счёт — оплата.

    Связь берётся только из реквизитов: реализация -> счёт по реквизиту шапки,
    поступление -> счёт по строке расшифровки платежа. Совпадение суммы связью
    не считается. Суммы в списках не нужны: список ведёт к документам.
    Строки открытого периода печатаются списком, закрытые — только числом:
    отбор по границе делает запрос.
    """
    r.head("7. СВЯЗИ ПРОДАЖ: АКТЫ, СЧЕТА, ОПЛАТЫ")
    if opt.locked_before is not None:
        r.w(f"Период до {opt.locked_before:%Y-%m-%d} закрыт: правки там невозможны.")
        r.w("Закрытые документы печатаются только числом и в сводку не попадают.")
    r.w("Поступления эквайринга и СБП законно бывают без счёта: вид операции в конце строки.")

    floor = None
    if opt.locked_before is not None:
        # Граница — литералом, не параметром: см. datetime_literal().
        floor = datetime_literal(dt.datetime.combine(opt.locked_before, dt.time()))

    for title, summary, alias, query in _LINK_LISTS:
        kind = title.startswith("Поступления")  # у поступления печатается вид операции
        open_cond = f" И {alias}.Дата >= {floor}" if floor else ""
        try:
            # Строка выборки — курсор: поля снимаем сразу, иначе все строки станут последней.
            items = [
                {"д": row.Дата, "н": b.s(row.Номер), "к": b.s(row.Контр) or "-",
                 "инн": b.s(row.ИНН) or "-", "вид": b.s(row.Вид) if kind else ""}
                for row in b.rows(query.replace("<период>", open_cond)
                                  + f"\n        УПОРЯДОЧИТЬ ПО {alias}.Дата УБЫВ")
            ]
            closed_count = 0
            if floor:
                row = b.one("// закрытые: " + title.lower() + "\n"
                            "ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК N ИЗ ("
                            + query.split("\n", 1)[1].replace(
                                "<период>", f" И {alias}.Дата < {floor}")
                            + ") КАК Т")
                closed_count = int(row.N) if row else 0
        except Exception as exc:
            r.fail(document_links.__name__, f"{title}: {exc}")
            continue
        r.w()
        r.w(f"   {title}: {len(items)}")
        open_count = 0
        for it in items:
            closed = is_locked(it["д"], opt)
            open_count += not closed
            line = f"      {it['д']:%Y-%m-%d}  {it['н']:<15}{it['к'][:29]:<30}{it['инн']:<12}"
            if kind:
                line += f"  {it['вид']}"
            r.w(line.rstrip() + ("  [закрыт]" if closed else ""))
        if closed_count:
            r.w(f"   из них в закрытом периоде: {closed_count}")
        if open_count:
            r.finding(f"{summary}: {open_count}")


ALL_CHECKS = [
    unposted_documents,
    bank_exchange,
    partners,
    balances,
    marked_for_deletion,
    month_closing,
    document_links,
]
