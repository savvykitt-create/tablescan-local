from tablescan_local.domain import ColumnRule, FieldRegion, NormalizedRect, TableTemplate


def test_template_round_trip() -> None:
    template = TableTemplate(
        id="template-1",
        name="Example",
        table_rect=NormalizedRect(0.1, 0.2, 0.8, 0.6),
        row_guides=[0.2, 0.4, 0.8],
        column_guides=[0.1, 0.5, 0.9],
        fields=[
            FieldRegion(
                "field-1", "Test", NormalizedRect(0.2, 0.1, 0.3, 0.05),
                source="fixed", fixed_value="PLANTAR",
            )
        ],
        column_rules=[ColumnRule("Animal", role="row_label"), ColumnRule("Value")],
    )

    restored = TableTemplate.from_dict(template.to_dict())

    assert restored.name == "Example"
    assert restored.fields[0].fixed_value == "PLANTAR"
    assert restored.column_rules[0].role == "row_label"
    assert restored.rows == 2
    assert restored.columns == 2

