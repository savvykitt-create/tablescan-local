import json
from pathlib import Path

from tablescan_local.storage import LocalStore


def test_first_launch_installs_three_templates_and_preserves_deletions(tmp_path):
    store = LocalStore(tmp_path)
    store.install_default_templates()
    templates = store.load_templates()
    assert {t.name for t in templates} == {'Von Frey', 'Plantar', 'Staircase'}
    assert all(t.auto_fit_rows and Path(t.reference_source_path).exists() for t in templates)
    assert all(Path(__file__).parents[1].joinpath('src/tablescan_local/default_templates', key + '.xlsx').exists()
               for key in ('von_frey', 'plantar', 'staircase'))
    removed = templates[0]
    store.delete_template(removed.id)
    store.install_default_templates()
    assert len(store.load_templates()) == 2
    assert store.job_count() == 0


def test_default_install_preserves_existing_family(tmp_path):
    store = LocalStore(tmp_path)
    store.install_default_templates()
    existing = store.load_templates()[0]
    existing.name = 'My edited form'
    store.save_template(existing)
    (tmp_path / '.default-templates-installed').unlink()
    store.install_default_templates()
    assert len(store.load_templates()) == 3
    assert any(t.name == 'My edited form' for t in store.load_templates())
