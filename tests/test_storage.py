from tablescan_local.domain import ColumnRule, NormalizedRect, TableTemplate
from tablescan_local.storage import LocalStore


def test_template_persistence(tmp_path) -> None:
    store = LocalStore(tmp_path / "data")
    template = TableTemplate(
        "id", "Saved", NormalizedRect(0.1, 0.1, 0.8, 0.8),
        [0.1, 0.5, 0.9], [0.1, 0.5, 0.9],
        column_rules=[ColumnRule("Label", role="row_label"), ColumnRule("Value")],
    )

    store.save_template(template)

    restored = store.load_templates()
    assert len(restored) == 1
    assert restored[0].name == "Saved"


def test_template_versions_and_reference_samples_are_immutable(tmp_path) -> None:
    store = LocalStore(tmp_path / "store")
    sample = tmp_path / "sample.png"
    sample.write_bytes(b"sample")
    template = TableTemplate("draft", "Test", NormalizedRect(0, 0, 1, 1), [0, 1], [0, 1], template_version=0)
    first = store.save_template_version(template, sample)
    first.name = "Changed"
    second = store.save_template_version(first, first.reference_source_path)
    saved = store.load_templates()
    assert sorted(item.template_version for item in saved) == [1, 2]
    assert next(item for item in saved if item.template_version == 1).name == "Test"
    assert second.reference_source_path != first.reference_source_path
    assert second.family_id == first.family_id
