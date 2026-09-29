# -*- coding: utf-8 -*-
"""Накопление и вывод отчёта."""
from __future__ import annotations

import os
from typing import Any


def money(value: Any) -> str:
    """Денежная сумма в русском формате: 1 234 567,89."""
    return f"{float(value or 0):,.2f}".replace(",", " ").replace(".", ",")


class Report:
    """Строки отчёта плюс отдельный список находок.

    Разделение принципиальное: в тело отчёта идёт всё, что посмотрели,
    включая заведомо безобидное. В findings — только то, с чем человек
    может что-то сделать. Сводка из findings и есть результат работы;
    если туда попадает шум, ей перестают верить.
    """

    def __init__(self, echo: bool = True) -> None:
        self.lines: list[str] = []
        self.findings: list[str] = []
        self.echo = echo

    def w(self, line: str = "") -> None:
        self.lines.append(line)
        if self.echo:
            print(line)

    def rule(self, char: str = "-", width: int = 100) -> None:
        self.w(char * width)

    def head(self, title: str) -> None:
        self.w()
        self.rule()
        self.w(title)
        self.rule()

    def finding(self, text: str) -> None:
        """Находка для итоговой сводки. Только устранимое."""
        self.findings.append(text)

    def summary(self) -> None:
        self.w()
        self.rule("=")
        self.w("ЧТО ПОСМОТРЕТЬ")
        self.rule("=")
        if not self.findings:
            self.w("   ничего требующего внимания не найдено")
        for i, text in enumerate(self.findings, 1):
            self.w(f"   {i}. {text}")

    def save(self, path: str) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
            fh.write("\n".join(self.lines) + "\n")
