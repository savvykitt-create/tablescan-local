from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from .constraints import ValueConstraints


FieldKind = Literal["text", "date", "integer", "numeric", "complex_numeric"]
FieldSource = Literal["ocr", "fixed"]
ExportMode = Literal["repeat", "column_group"]
ColumnRole = Literal["data", "header", "row_label", "ignored"]


def excel_column_name(index: int) -> str:
    """Return the one-based spreadsheet column label for a zero-based index."""
    if index < 0:
        raise ValueError("Column index cannot be negative")
    label = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        label = chr(65 + remainder) + label
    return label


@dataclass(slots=True)
class NormalizedRect:
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_pixels(cls, rect: tuple[int, int, int, int], image_shape: tuple[int, ...]) -> "NormalizedRect":
        x, y, width, height = rect
        image_height, image_width = image_shape[:2]
        return cls(x / image_width, y / image_height, width / image_width, height / image_height)

    def to_pixels(self, image_shape: tuple[int, ...]) -> tuple[int, int, int, int]:
        image_height, image_width = image_shape[:2]
        x = max(0, min(image_width - 1, round(self.x * image_width)))
        y = max(0, min(image_height - 1, round(self.y * image_height)))
        width = max(1, min(image_width - x, round(self.width * image_width)))
        height = max(1, min(image_height - y, round(self.height * image_height)))
        return x, y, width, height


@dataclass(slots=True)
class ColumnRule:
    name: str
    role: ColumnRole = "data"
    value_format: FieldKind = "complex_numeric"
    decimal_separator: Literal["dot_or_comma", "dot", "comma"] = "dot_or_comma"
    minimum: float | None = None
    maximum: float | None = None
    allow_empty: bool = True
    preserve_complex_notation: bool = True
    decimal_places: int | None = None
    allowed_values: list[str] = field(default_factory=list)
    expected_minimum: float | None = None
    expected_maximum: float | None = None
    suggest_missing_decimal: bool = False
    prefer_expected_range: bool = False
    require_decimal: bool = False

    def constraints(self) -> ValueConstraints:
        return ValueConstraints(**{key: value for key, value in asdict(self).items() if key in ValueConstraints.__dataclass_fields__})


@dataclass(slots=True)
class CellRuleRegion:
    id: str
    name: str
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    constraints: ValueConstraints = field(default_factory=ValueConstraints)
    color: str = "#4F46E5"

    def contains(self, row: int, column: int) -> bool:
        return self.row_start <= row <= self.row_end and self.column_start <= column <= self.column_end

    def address(self) -> str:
        start = f"{excel_column_name(self.column_start)}{self.row_start + 1}"
        end = f"{excel_column_name(self.column_end)}{self.row_end + 1}"
        return start if start == end else f"{start}:{end}"


@dataclass(slots=True)
class FieldRegion:
    id: str
    name: str
    rect: NormalizedRect
    kind: FieldKind = "text"
    recognition: Literal["printed", "handwritten", "numeric"] = "printed"
    source: FieldSource = "ocr"
    fixed_value: str = ""
    export_mode: ExportMode = "repeat"
    required: bool = False
    column_start: int | None = None
    column_end: int | None = None
    color: str = "#2563EB"


@dataclass(slots=True)
class TableTemplate:
    id: str
    name: str
    table_rect: NormalizedRect
    row_guides: list[float]
    column_guides: list[float]
    header_rows: int = 0
    row_label_columns: int = 1
    detect_crossed_rows: bool = True
    fields: list[FieldRegion] = field(default_factory=list)
    column_rules: list[ColumnRule] = field(default_factory=list)
    schema_version: int = 3
    rotation_degrees: int = 0
    cell_rules: list[CellRuleRegion] = field(default_factory=list)
    template_version: int = 1
    family_id: str = ""
    reference_source_path: str = ""
    reference_page_aspect: float | None = None

    def __post_init__(self) -> None:
        if not self.family_id:
            self.family_id = self.id

    @property
    def rows(self) -> int:
        return max(0, len(self.row_guides) - 1)

    @property
    def columns(self) -> int:
        return max(0, len(self.column_guides) - 1)

    def ensure_column_rules(self) -> None:
        while len(self.column_rules) < self.columns:
            index = len(self.column_rules)
            role: ColumnRole = "row_label" if index < self.row_label_columns else "data"
            self.column_rules.append(ColumnRule(name=f"Column {index + 1}", role=role))
        self.column_rules = self.column_rules[: self.columns]

    def value_constraints(self, row: int, column: int) -> tuple[ValueConstraints, str]:
        matches = [
            (index, region)
            for index, region in enumerate(self.cell_rules)
            if region.contains(row, column)
        ]
        if matches:
            # A smaller block is more specific than a larger one; a newer rule
            # wins only when both cover exactly the same number of cells.
            _, region = min(
                matches,
                key=lambda item: (
                    (item[1].row_end - item[1].row_start + 1)
                    * (item[1].column_end - item[1].column_start + 1),
                    -item[0],
                ),
            )
            return region.constraints, f"{region.name} · {region.address()}"
        rule = self.column_rules[column]
        return rule.constraints(), rule.name

    def validate_value_rules(self) -> None:
        self.ensure_column_rules()
        for rule in self.column_rules:
            rule.constraints().validate()
        for region in self.cell_rules:
            if not (0 <= region.row_start <= region.row_end < self.rows and 0 <= region.column_start <= region.column_end < self.columns):
                raise ValueError(f"Правило «{region.name}» выходит за текущую сетку; измените или удалите его")
            region.constraints.validate()
        for left_index, left in enumerate(self.cell_rules):
            left_area = (left.row_end - left.row_start + 1) * (left.column_end - left.column_start + 1)
            for right in self.cell_rules[left_index + 1:]:
                right_area = (right.row_end - right.row_start + 1) * (right.column_end - right.column_start + 1)
                overlaps = not (
                    left.row_end < right.row_start or right.row_end < left.row_start
                    or left.column_end < right.column_start or right.column_end < left.column_start
                )
                same_rect = (
                    left.row_start, left.row_end, left.column_start, left.column_end
                ) == (
                    right.row_start, right.row_end, right.column_start, right.column_end
                )
                if overlaps and left_area == right_area and not same_rect and left.constraints != right.constraints:
                    raise ValueError(
                        f"Правила «{left.name}» и «{right.name}» одинаково специфичны и перекрываются; "
                        "разделите области или сделайте одну из них точнее"
                    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TableTemplate":
        return cls(
            id=data["id"],
            name=data["name"],
            table_rect=NormalizedRect(**data["table_rect"]),
            row_guides=[float(value) for value in data["row_guides"]],
            column_guides=[float(value) for value in data["column_guides"]],
            header_rows=int(data.get("header_rows", 1)),
            row_label_columns=int(data.get("row_label_columns", 1)),
            detect_crossed_rows=bool(data.get("detect_crossed_rows", True)),
            fields=[
                FieldRegion(
                    **{key: value for key, value in item.items() if key != "rect"},
                    rect=NormalizedRect(**item["rect"]),
                )
                for item in data.get("fields", [])
            ],
            column_rules=[ColumnRule(**item) for item in data.get("column_rules", [])],
            schema_version=int(data.get("schema_version", 1)),
            rotation_degrees=int(data.get("rotation_degrees", 0)),
            cell_rules=[CellRuleRegion(**{k: v for k, v in item.items() if k != "constraints"}, constraints=ValueConstraints(**item.get("constraints", {}))) for item in data.get("cell_rules", [])],
            template_version=int(data.get("template_version", 1)),
            family_id=str(data.get("family_id") or data["id"]),
            reference_source_path=str(data.get("reference_source_path", "")),
            reference_page_aspect=(float(data["reference_page_aspect"]) if data.get("reference_page_aspect") is not None else None),
        )


NON_BLOCKING_OCR_FLAGS = frozenset({
    # These flags describe checks that contributed to a result.  They are
    # useful in the audit trail, but do not by themselves mean that two
    # plausible readings remain unresolved.
    "alternative_selected",
    "auto_excluded_crossed_row",
    "constrained_decoder_used",
    "crop_retry_contributed",
    "crossed_out_row",
    "decimal_separator_detected",
    "digit_verifier_agrees",
    "digit_verifier_disagreement",
    "high_accuracy_consensus",
    "multistage_cascade",
    "non_numeric_mark_row",
    "numeric_verification_required",
    "repeated_digit_shape_agrees",
    "rule_selected_alternative",
    "separator_not_visually_confirmed",
    "decimal_boundary_inferred_from_glyphs",
    "value_rule_review_required",
    "visible_digit_count_used",
})


def flags_need_review(flags: list[str]) -> bool:
    """Return whether flags contain an unresolved error or close decision.

    Secondary verifiers are intentionally advisory when the main multi-model
    result is otherwise stable.  A low confidence, unstable consensus, rule
    conflict, outlier, or other unknown safety flag still requires review.
    """
    return any(flag not in NON_BLOCKING_OCR_FLAGS for flag in flags)


@dataclass(slots=True)
class CellResult:
    row: int
    column: int
    raw_text: str
    final_text: str
    confidence: float
    crop_path: str = ""
    flags: list[str] = field(default_factory=list)
    status: Literal["automatic", "confirmed", "corrected", "excluded"] = "automatic"
    alternatives: str = ""
    applied_rule: str = ""
    suggested_text: str = ""

    @property
    def needs_review(self) -> bool:
        return flags_need_review(self.flags) and self.status == "automatic"


@dataclass(slots=True)
class FieldResult:
    region_id: str
    name: str
    raw_text: str
    final_text: str
    confidence: float
    crop_path: str = ""
    flags: list[str] = field(default_factory=list)
    status: Literal["automatic", "confirmed", "corrected"] = "automatic"
    alternatives: str = ""

    @property
    def needs_review(self) -> bool:
        return flags_need_review(self.flags) and self.status == "automatic"


@dataclass(slots=True)
class PageResult:
    page_index: int
    source_file: str
    cells: list[CellResult]
    fields: list[FieldResult]
    excluded_rows: list[int] = field(default_factory=list)

    def cell(self, row: int, column: int) -> CellResult | None:
        return next((cell for cell in self.cells if cell.row == row and cell.column == column), None)

    @property
    def unresolved_count(self) -> int:
        return sum(cell.needs_review for cell in self.cells) + sum(item.needs_review for item in self.fields)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageResult":
        return cls(
            page_index=int(data["page_index"]),
            source_file=data["source_file"],
            cells=[CellResult(**item) for item in data.get("cells", [])],
            fields=[FieldResult(**item) for item in data.get("fields", [])],
            excluded_rows=[int(value) for value in data.get("excluded_rows", [])],
        )


@dataclass(slots=True)
class JobResult:
    source_path: str
    template: TableTemplate
    pages: list[PageResult]
    model_version: str = "rapidocr-onnxruntime-1.4"

    @property
    def source_name(self) -> str:
        return Path(self.source_path).name

    @property
    def unresolved_count(self) -> int:
        return sum(page.unresolved_count for page in self.pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "template": self.template.to_dict(),
            "pages": [page.to_dict() for page in self.pages],
            "model_version": self.model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobResult":
        return cls(
            source_path=data["source_path"],
            template=TableTemplate.from_dict(data["template"]),
            pages=[PageResult.from_dict(item) for item in data.get("pages", [])],
            model_version=data.get("model_version", "unknown"),
        )
