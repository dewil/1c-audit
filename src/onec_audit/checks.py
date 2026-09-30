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

    since_year: int = 2024
    """С какого года смотреть обмен с банком."""

    locked_before: int = 0
    """Год, до которого правки запрещены (дата запрета изменения).

    Находки раньше этой границы печатаются с пометкой, но в сводку не идут:
    исправить их всё равно нельзя, а сводку они забивают. 0 — считать
    всю историю изменяемой.
    """

    all_docs: bool = False
    """Сканировать все типы документов, а не короткий список."""

    fast_doc_list: list[str] = field(default_factory=lambda: [
        "РеализацияТоваровУслуг", "СчетНаОплатуПокупателю", "ПоступлениеТоваровУслуг",
        "ПоступлениеНаРасчетныйСчет", "СписаниеСРасчетногоСчета", "ПлатежноеПоручение",
        "АктСверкиВзаиморасчетов", "ВводНачальныхОстатков", "СчетНаОплатуПоставщика",
        "РегламентнаяОперация", "ДокументРасчетовСКонтрагентом", "ОперацияБух",
    ])


# Типы, для которых «непроведены все» — НЕ признак служебного типа.
# У регламентных операций это означает, что закрытие месяца не выполнялось,
# то есть ровно ту находку, ради которой проверка и пишется.
# Эвристика, которая прячет находки, хуже её отсутствия.
NEVER_STRUCTURAL = {"РегламентнаяОперация"}


# ---------------------------------------------------------------------------
def unposted_documents(b: Base, r: Report, opt: Options) -> None:
    """Непроведённые документы.

    Непроведённый документ не делает проводок и на учёт не влияет. Вопрос
    не в учёте, а в том, состоялась ли операция и получил ли контрагент
    первичный документ.
    """
    r.head("1. НЕПРОВЕДЁННЫЕ ДОКУМЕНТЫ")
    if opt.locked_before:
        r.w(f"Период до {opt.locked_before} года закрыт: правки там невозможны.")
        r.w("Такие строки помечены [закрыт] и в сводку не попадают.")
    r.w()

    doc_names = b.names(b.md.Документы)
    scan = doc_names if opt.all_docs else [d for d in opt.fast_doc_list if d in doc_names]
    if opt.all_docs:
        r.w(f"(сканирую все {len(doc_names)} типов документов)")

    year_floor = opt.locked_before or 1
    rows = []
    failed = False
    for name in scan:
        try:
            row = b.one(
                f"""
                ВЫБРАТЬ
                    КОЛИЧЕСТВО(*) КАК Всего,
                    СУММА(ВЫБОР КОГДА НЕ Проведен ТОГДА 1 ИНАЧЕ 0 КОНЕЦ) КАК Непров,
                    СУММА(ВЫБОР КОГДА НЕ Проведен И ГОД(Дата) >= &Год ТОГДА 1 ИНАЧЕ 0 КОНЕЦ) КАК Открыт
                ИЗ Документ.{name}
                ГДЕ НЕ ПометкаУдаления
                """,
                Год=year_floor,
            )
        except Exception as exc:
            r.fail(unposted_documents.__name__, f"{name}: {exc}")
            failed = True
            continue
        if not row or not row.Непров:
            continue
        rows.append({"тип": name, "всего": int(row.Всего),
                     "непров": int(row.Непров), "открыт": int(row.Открыт or 0)})

    structural = [x for x in rows
                  if x["непров"] == x["всего"] and x["всего"] > 5
                  and x["тип"] not in NEVER_STRUCTURAL]
    anomalous = [x for x in rows if x not in structural]

    if not anomalous:
        if not failed:
            r.w("   непроведённых документов нет")
    else:
        r.w(f"   {'Тип документа':<38}{'всего':>8}{'непров.':>10}{'в открытом':>12}")
        for x in sorted(anomalous, key=lambda v: -v["непров"]):
            r.w(f"   {x['тип']:<38}{x['всего']:>8}{x['непров']:>10}{x['открыт']:>12}")

    if structural:
        r.w()
        r.w("   Не считаю отклонением — непроведены ВСЕ документы этих типов,")
        r.w("   значит тип так и работает (служебные, подчинённые и т.п.):")
        for x in structural:
            r.w(f"     {x['тип']:<38}{x['всего']} из {x['всего']}")

    for_summary = [x for x in anomalous if x["тип"] not in NEVER_STRUCTURAL]
    if len(for_summary) != len(anomalous):
        r.w()
        r.w("   Регламентные операции здесь не считаются — это закрытие месяца, см. раздел 6.")
    open_total = sum(x["открыт"] for x in for_summary)
    all_total = sum(x["непров"] for x in for_summary)
    if open_total:
        r.finding(f"непроведённых в открытом периоде: {open_total} (всего {all_total})")
    elif all_total:
        r.w()
        r.w(f"   Все {all_total} непроведённых — в закрытом периоде, правки невозможны.")

    _unposted_sales_detail(b, r, opt, anomalous)


def _unposted_sales_detail(b: Base, r: Report, opt: Options, anomalous: list) -> None:
    """Детализация по продажам с проверкой, приходили ли деньги."""
    for doc in ("РеализацияТоваровУслуг", "СчетНаОплатуПокупателю"):
        if not any(x["тип"] == doc for x in anomalous):
            continue
        r.w()
        r.w(f"   ДЕТАЛИЗАЦИЯ: {doc}")
        r.w("   " + "." * 96)
        r.w(f"   {'Дата':<12}{'Номер':<15}{'Контрагент':<30}{'Сумма':>13}  Оплата")

        items = [
            {"д": row.Дата, "н": b.s(row.Номер), "ссылка": row.Контрагент,
             "к": b.s(row.КонтрТекст), "сум": float(row.СуммаДокумента or 0)}
            for row in b.rows(f"""
                ВЫБРАТЬ Дата, Номер, Контрагент, СуммаДокумента,
                       ПРЕДСТАВЛЕНИЕ(Контрагент) КАК КонтрТекст
                ИЗ Документ.{doc}
                ГДЕ НЕ Проведен И НЕ ПометкаУдаления
                УПОРЯДОЧИТЬ ПО Дата УБЫВ
            """)
        ]
        no_pay, locked = [], 0
        for it in items:
            is_locked = bool(opt.locked_before and it["д"].year < opt.locked_before)
            locked += is_locked
            paid = ""
            try:
                # Границы окна — литералами, не параметрами: см. datetime_literal().
                hit = b.one(
                    f"""
                    ВЫБРАТЬ ПЕРВЫЕ 1 Дата
                    ИЗ Документ.ПоступлениеНаРасчетныйСчет
                    ГДЕ Проведен И НЕ ПометкаУдаления
                      И Контрагент = &Контр И СуммаДокумента = &Сум
                      И Дата МЕЖДУ {datetime_literal(it["д"] - dt.timedelta(days=90))}
                                И {datetime_literal(it["д"] + dt.timedelta(days=400))}
                    УПОРЯДОЧИТЬ ПО Дата
                    """,
                    Контр=it["ссылка"], Сум=it["сум"],
                )
                if hit:
                    paid = f"ДА {hit.Дата:%Y-%m-%d}"
            except Exception as exc:
                r.fail(unposted_documents.__name__, f"поиск оплаты {doc}: {exc}")
                paid = "?"
            if not paid:
                paid = "НЕТ"
                if not is_locked:
                    no_pay.append(it)
            mark = "[закрыт]" if is_locked else ""
            r.w(f"   {it['д']:%Y-%m-%d}  {it['н']:<15}{it['к'][:29]:<30}"
                f"{money(it['сум']):>13}  {paid:<17}{mark}")

        r.w(f"   итого {len(items)} шт, из них в закрытом периоде {locked}")
        r.w(f"   в открытом периоде без совпадения по оплате: {len(no_pay)} шт "
            f"на {money(sum(x['сум'] for x in no_pay))}")
        # Оговорка печатается рядом с находкой, а не сноской в конце отчёта.
        r.w("   ОГОВОРКА: «НЕТ» значит только «нет платежа на точно такую же сумму")
        r.w("   в окне −90/+400 дней». Оплата могла прийти частями или одной")
        r.w("   платёжкой за несколько документов — это подсказка, а не приговор.")
        if no_pay:
            r.finding(f"{doc} без совпадения по оплате: {len(no_pay)} шт "
                      f"на {money(sum(x['сум'] for x in no_pay))}")


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
        row = b.one('ВЫБРАТЬ КОЛИЧЕСТВО(*) КАК N ИЗ Справочник.Контрагенты '
                    'ГДЕ ИНН = "" И НЕ ПометкаУдаления')
        n = int(row.N) if row else 0
        r.w(f"   без ИНН: {n}")
        if n:
            r.finding(f"контрагентов без ИНН: {n}")
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

    today = dt.date.today()
    start_year = opt.locked_before or today.year - 1
    month = dt.datetime(start_year, 1, 1)
    last = dt.datetime(today.year, today.month, 1) - dt.timedelta(seconds=1)

    r.w(f"   Проверяются завершённые месяцы: {month:%Y-%m} .. {last:%Y-%m}")
    r.w()
    r.w(f"   {'Месяц':<10}{'операций':>9}{'проведено':>11}   Состояние")

    unclosed = []
    while month <= last:
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
        month = (month + dt.timedelta(days=32)).replace(day=1)

    if unclosed:
        r.finding(f"не закрыто месяцев: {len(unclosed)} "
                  f"(с {unclosed[0]:%Y-%m} по {unclosed[-1]:%Y-%m})")
        r.w()
        r.w("   Закрывается: Операции → Закрытие месяца.")
        r.w("   Помнить: правка документа задним числом снимает закрытие того")
        r.w("   месяца и всех следующих — их надо перезакрыть.")


ALL_CHECKS = [
    unposted_documents,
    bank_exchange,
    partners,
    balances,
    marked_for_deletion,
    month_closing,
]
