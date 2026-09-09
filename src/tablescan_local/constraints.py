"""User-declared value rules. Hard limits and expectations are kept separate."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

SIMPLE = re.compile(r"[+\-−]?\d+(?:[.,]\d+)?")
COMPLEX = re.compile(r"(?:[<>≤≥]=?\s*)?[+\-−]?\d+(?:[.,]\d+)?(?:\s*(?:±|\+/-|[-–])\s*[+\-−]?\d+(?:[.,]\d+)?)?(?:[eE][+\-−]?\d+)?%?")


@dataclass(slots=True)
class ValueConstraints:
    value_format: str = "complex_numeric"
    minimum: float | None = None
    maximum: float | None = None
    decimal_places: int | None = None
    allowed_values: list[str] = field(default_factory=list)
    expected_minimum: float | None = None
    expected_maximum: float | None = None
    allow_empty: bool = True
    suggest_missing_decimal: bool = False
    prefer_expected_range: bool = False
    require_decimal: bool = False

    def validate(self) -> None:
        if self.value_format not in {"complex_numeric", "numeric", "integer", "text", "date"}:
            raise ValueError("Неизвестный тип значения")
        for low, high in ((self.minimum, self.maximum), (self.expected_minimum, self.expected_maximum)):
            if any(v is not None and not Decimal(str(v)).is_finite() for v in (low, high)):
                raise ValueError("Границы должны быть конечными числами")
            if low is not None and high is not None and low > high:
                raise ValueError("Нижняя граница не может быть больше верхней")
        if self.decimal_places is not None and (type(self.decimal_places) is not int or not 0 <= self.decimal_places <= 8):
            raise ValueError("Допустимо от 0 до 8 знаков после разделителя")
        if self.value_format == "integer" and self.decimal_places not in (None, 0):
            raise ValueError("Целое число не может иметь дробные знаки")
        if self.require_decimal and self.value_format != "numeric":
            raise ValueError("Обязательная десятичная часть доступна только для десятичного числа")
        if self.require_decimal and self.decimal_places == 0:
            raise ValueError("Для обязательной десятичной части нужно хотя бы один знак после разделителя")
        if self.value_format not in {"integer", "numeric"} and (self.decimal_places is not None or self.suggest_missing_decimal):
            raise ValueError("Число дробных знаков доступно только для простых чисел")
        if self.suggest_missing_decimal and not self.decimal_places:
            raise ValueError("Для предложения разделителя задайте число дробных знаков")
        if self.prefer_expected_range and self.expected_minimum is None and self.expected_maximum is None:
            raise ValueError("Для предпочтения обычного диапазона задайте хотя бы одну его границу")
        if self.value_format in {"text", "date"} and any(v is not None for v in (self.minimum, self.maximum, self.expected_minimum, self.expected_maximum)):
            raise ValueError("Числовые диапазоны неприменимы к тексту и дате")
        if self.allowed_values and not any(not self.hard_errors(v, check_allowed=False) for v in self.allowed_values):
            raise ValueError("Ни одно разрешённое значение не соответствует формату и диапазону")

    def hard_errors(self, text: str, *, check_allowed: bool = True) -> list[str]:
        text = text.strip()
        if not text:
            return [] if self.allow_empty else ["required_cell_empty"]
        errors = []
        simple = SIMPLE.fullmatch(text)
        if self.value_format == "integer" and not re.fullmatch(r"[+\-−]?\d+", text):
            errors.append("expected_integer")
        elif self.value_format == "numeric" and not simple:
            errors.append("expected_decimal_number")
        elif self.value_format == "complex_numeric" and not COMPLEX.fullmatch(text):
            errors.append("invalid_numeric_format")
        if self.decimal_places is not None:
            normalized = text.replace(",", ".")
            places = len(normalized.split(".")[1]) if simple and "." in normalized else 0
            if not simple or places != self.decimal_places:
                errors.append("decimal_places_mismatch")
        if self.require_decimal and (not simple or not any(mark in text for mark in ".,")):
            errors.append("decimal_part_required")
        if simple and self.value_format not in {"text", "date"}:
            number = Decimal(text.replace(",", ".").replace("−", "-"))
            if self.minimum is not None and number < Decimal(str(self.minimum)):
                errors.append("below_minimum")
            if self.maximum is not None and number > Decimal(str(self.maximum)):
                errors.append("above_maximum")
        elif self.value_format == "complex_numeric" and (self.minimum is not None or self.maximum is not None):
            errors.append("range_not_checkable")
        if check_allowed and self.allowed_values:
            allowed = {self.key(v) for v in self.allowed_values}
            if self.key(text) not in allowed:
                errors.append("value_not_allowed")
        return errors

    def warnings(self, text: str) -> list[str]:
        if self.value_format in {"text", "date"} or not SIMPLE.fullmatch(text.strip()):
            return []
        number = Decimal(text.strip().replace(",", ".").replace("−", "-"))
        if ((self.expected_minimum is not None and number < Decimal(str(self.expected_minimum))) or
                (self.expected_maximum is not None and number > Decimal(str(self.expected_maximum)))):
            return ["outside_expected_range"]
        return []

    def key(self, value: str) -> str:
        value = value.strip()
        if self.value_format in {"numeric", "integer", "complex_numeric"} and SIMPLE.fullmatch(value):
            return str(Decimal(value.replace(",", ".").replace("−", "-")).normalize())
        return value

    def decimal_proposal(self, text: str) -> str | None:
        if not self.suggest_missing_decimal or not self.decimal_places or not re.fullmatch(r"[+\-−]?\d+", text):
            return None
        sign = text[0] if text[0] in "+-−" else ""
        digits = text[len(sign):]
        places = self.decimal_places
        # Insertion only: never delete a leading 1, replace a glyph or move an
        # already visible separator. The proposal is NEVER auto-confirmed.
        if len(digits) <= places:
            return None
        proposal = sign + digits[:-places] + "." + digits[-places:]
        return proposal if not self.hard_errors(proposal) else None

    def separator_reclassification(self, text: str) -> str | None:
        """Reclassify one narrow OCR ``1`` as the required separator.

        This is deliberately possible only at the exact position implied by an
        explicit decimal-place rule.  It never drops another digit and the
        caller must mark the result for human review.
        """
        if not self.suggest_missing_decimal or not self.decimal_places:
            return None
        text = text.strip().replace(",", ".").replace("−", "-")
        if not re.fullmatch(r"[+\-]?\d+", text):
            return None
        sign = text[0] if text[0] in "+-" else ""
        digits = text[len(sign):]
        separator_index = len(digits) - self.decimal_places - 1
        if separator_index <= 0 or separator_index >= len(digits) or digits[separator_index] != "1":
            return None
        proposal = sign + digits[:separator_index] + "." + digits[separator_index + 1:]
        return proposal if not self.hard_errors(proposal) else None

    def summary(self) -> str:
        labels = {"numeric": "Число", "integer": "Целое", "text": "Текст", "date": "Дата", "complex_numeric": "Число / сложная запись"}
        parts = [labels[self.value_format]]
        if self.decimal_places is not None:
            parts.append(f"дробных знаков: {self.decimal_places}")
        if self.minimum is not None or self.maximum is not None:
            parts.append(f"от {self.minimum if self.minimum is not None else '−∞'} до {self.maximum if self.maximum is not None else '+∞'}")
        if self.allowed_values:
            parts.append("из списка: " + "; ".join(self.allowed_values))
        if self.expected_minimum is not None or self.expected_maximum is not None:
            parts.append(f"обычно {self.expected_minimum}…{self.expected_maximum}")
        if self.suggest_missing_decimal:
            parts.append("предлагать пропущенный разделитель (со сверкой)")
        if self.require_decimal:
            parts.append("десятичная часть обязательна")
        if self.prefer_expected_range:
            parts.append("предпочитать OCR-кандидата из обычного диапазона (со сверкой)")
        return "; ".join(parts)
