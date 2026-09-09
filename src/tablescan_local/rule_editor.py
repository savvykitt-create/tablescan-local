"""Native, reusable value-rule editor; no OCR or file writes inside the dialog."""
from dataclasses import asdict

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout, QWidget

from .constraints import ValueConstraints
from .ocr import OcrValue, constrain_reading


def optional_number(text: str) -> float | None:
    text = text.strip().replace(",", ".").replace("−", "-")
    return float(text) if text else None


class ValueRuleDialog(QDialog):
    def __init__(self, rule: ValueConstraints, parent=None, *, title: str = "Правила значения", region=None, rows=1, columns=1):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(570)
        self.rule = ValueConstraints(**asdict(rule))
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.region = region
        if region is not None:
            self.region_name = QLineEdit(region.name)
            form.addRow("Название", self.region_name)
            self.coordinates = []
            for label, start, end, maximum in (("Строки (включительно)", region.row_start, region.row_end, rows), ("Столбцы (включительно)", region.column_start, region.column_end, columns)):
                box = QWidget(); row = QHBoxLayout(box); row.setContentsMargins(0, 0, 0, 0)
                for value in (start, end):
                    spin = QSpinBox(); spin.setRange(1, maximum); spin.setValue(value + 1)
                    self.coordinates.append(spin); row.addWidget(spin)
                form.addRow(label, box)
        self.preset = QComboBox()
        self.preset.addItems(["Выбрать готовый формат…", "Дробь 0–100, один знак (34,4)", "Дробь 0–1000, два знака (34,45)", "Целое число ≥ 0", "Текст / обозначение"])
        self.kind = QComboBox()
        for text, code in (("Десятичное число", "numeric"), ("Целое число", "integer"), ("Текст", "text"), ("Дата (текст)", "date"), ("Сложная запись: ±, <, %, …", "complex_numeric")):
            self.kind.addItem(text, code)
        self.places = QSpinBox(); self.places.setRange(-1, 8); self.places.setSpecialValueText("Любое")
        self.minimum = QLineEdit(); self.maximum = QLineEdit()
        self.expected_minimum = QLineEdit(); self.expected_maximum = QLineEdit()
        self.allowed = QLineEdit(); self.allowed.setPlaceholderText("Например: 0; 1; 2; 3 — пусто: без списка")
        self.allow_empty = QCheckBox("Ячейка может быть пустой")
        self.suggest = QCheckBox("Предлагать пропущенный разделитель (требует сверки)")
        self.require_decimal = QCheckBox("Десятичная часть обязательна")
        self.prefer_expected = QCheckBox("Предпочитать OCR-вариант из обычного диапазона (требует сверки)")
        form.addRow("Готовый формат", self.preset)
        form.addRow("Тип значения", self.kind)
        form.addRow("Ровно знаков после точки/запятой", self.places)
        for label, low, high in (("Разрешено от / до", self.minimum, self.maximum), ("Обычно от / до (только предупреждение)", self.expected_minimum, self.expected_maximum)):
            box = QWidget(); row = QHBoxLayout(box); row.setContentsMargins(0, 0, 0, 0)
            low.setPlaceholderText("Без нижней границы"); high.setPlaceholderText("Без верхней границы")
            row.addWidget(low); row.addWidget(high); form.addRow(label, box)
        form.addRow("Только значения из списка", self.allowed)
        form.addRow(self.allow_empty); form.addRow(self.require_decimal); form.addRow(self.suggest); form.addRow(self.prefer_expected)
        outer.addLayout(form)
        note = QLabel("Точка и запятая равнозначны. Жёсткие границы исключают невозможные варианты. Обычный диапазон меняет порядок только при включённом предпочтении и только среди реально прочитанных кандидатов; результат всё равно требует сверки.")
        note.setWordWrap(True); outer.addWidget(note)
        self.sample = QLineEdit("344")
        form.addRow("Проверить пример", self.sample)
        self.preview = QLabel(); self.preview.setWordWrap(True); outer.addWidget(self.preview)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("Применить правило")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self._accept_rule); self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self._load(rule)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        for widget in (self.minimum, self.maximum, self.expected_minimum, self.expected_maximum, self.allowed, self.sample):
            widget.textChanged.connect(self._preview)
        self.places.valueChanged.connect(self._preview)
        self.allow_empty.toggled.connect(self._preview); self.require_decimal.toggled.connect(self._preview)
        self.suggest.toggled.connect(self._preview); self.prefer_expected.toggled.connect(self._preview)
        self._preview()

    def _load(self, rule):
        self.kind.setCurrentIndex(self.kind.findData(rule.value_format))
        self.places.setValue(-1 if rule.decimal_places is None else rule.decimal_places)
        for name in ("minimum", "maximum", "expected_minimum", "expected_maximum"):
            value = getattr(rule, name); getattr(self, name).setText("" if value is None else str(value))
        self.allowed.setText("; ".join(rule.allowed_values))
        self.allow_empty.setChecked(rule.allow_empty); self.suggest.setChecked(rule.suggest_missing_decimal)
        self.require_decimal.setChecked(rule.require_decimal)
        self.prefer_expected.setChecked(rule.prefer_expected_range)

    def _preset_changed(self, index):
        presets = {1: ValueConstraints("numeric", 0, 100, 1, suggest_missing_decimal=True, require_decimal=True),
                   2: ValueConstraints("numeric", 0, 1000, 2, suggest_missing_decimal=True, require_decimal=True),
                   3: ValueConstraints("integer", minimum=0), 4: ValueConstraints("text")}
        if index in presets:
            self._load(presets[index]); self._preview()

    def _kind_changed(self):
        if self.kind.currentData() not in {"numeric", "integer"}:
            self.places.setValue(-1); self.suggest.setChecked(False); self.require_decimal.setChecked(False)
        if self.kind.currentData() in {"text", "date"}:
            for w in (self.minimum, self.maximum, self.expected_minimum, self.expected_maximum): w.clear()
            self.prefer_expected.setChecked(False)
        if self.kind.currentData() == "integer":
            self.places.setValue(0); self.suggest.setChecked(False); self.require_decimal.setChecked(False)
        self._preview()

    def read_rule(self) -> ValueConstraints:
        rule = ValueConstraints(
            value_format=self.kind.currentData(),
            decimal_places=None if self.places.value() == -1 else self.places.value(),
            allowed_values=[v.strip() for v in self.allowed.text().split(";") if v.strip()],
            allow_empty=self.allow_empty.isChecked(), suggest_missing_decimal=self.suggest.isChecked(),
            prefer_expected_range=self.prefer_expected.isChecked(),
            require_decimal=self.require_decimal.isChecked(),
            **{name: optional_number(getattr(self, name).text()) for name in ("minimum", "maximum", "expected_minimum", "expected_maximum")},
        )
        rule.validate()
        return rule

    def _preview(self):
        try:
            rule = self.read_rule()
            reading = constrain_reading(OcrValue(self.sample.text(), 0), rule)
            if "separator_reclassified_from_one" in reading.flags:
                message = f"{self.sample.text()} → предложение {reading.text}. Узкий штрих 1 проверяется как разделитель; обязательно сравните с изображением."
            elif "separator_inferred_from_rule" in reading.flags:
                message = f"{self.sample.text()} → предложение {reading.text}. Разделитель предполагается, проверьте изображение."
            elif rule.hard_errors(reading.text):
                message = "Пример не соответствует правилу. Без подходящего прочтения OCR он останется спорным."
            elif rule.warnings(reading.text):
                message = "Допустимо, но вне обычного диапазона. Значение не подменяется."
            else:
                message = "Пример соответствует формату. Перед экспортом всё равно нужна сверка."
            self.preview.setText(message)
        except (ValueError, TypeError) as exc:
            self.preview.setText(f"Проверьте настройку: {exc}")

    def _accept_rule(self):
        try:
            rule = self.read_rule()
            if self.region is not None:
                rs, re, cs, ce = [spin.value() - 1 for spin in self.coordinates]
                if rs > re or cs > ce:
                    raise ValueError("Начало выделения должно быть раньше конца")
                self.selection = (rs, re, cs, ce)
            self.rule = rule
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Некорректное правило", str(exc)); return
        self.accept()
