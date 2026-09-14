from __future__ import annotations

from .i18n import tr, fmt, join_text
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .domain import JobResult, TableTemplate


class LocalStore:
    def install_default_templates(self) -> None:
        """Seed bundled forms once; retain user edits and intentional deletions."""
        marker = self.root / '.default-templates-installed'
        if marker.exists():
            return
        bundled = Path(__file__).parent / 'default_templates'
        existing_families = {item.family_id for item in self.load_templates()}
        for path in sorted(bundled.glob('*.json')):
            template = TableTemplate.from_dict(json.loads(path.read_text(encoding='utf-8')))
            if template.family_id not in existing_families:
                self.save_template(template, bundled / f'{path.stem}.pdf')
        marker.write_text('installed\n', encoding='utf-8')

    def __init__(self, root: Path) -> None:
        self.root = root
        self.jobs_dir = root / "jobs"
        self.templates_dir = root / "templates"
        self.template_samples_dir = root / "template-samples"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.template_samples_dir.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(root / "tablescan.db")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source_name TEXT NOT NULL,
                source_path TEXT NOT NULL,
                stored_source_path TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def import_source(self, source: str | Path) -> tuple[str, Path]:
        source_path = Path(source)
        job_id = str(uuid4())
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        copied = job_dir / source_path.name
        try:
            shutil.copy2(source_path, copied)
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.connection:
                self.connection.execute(
                    "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, source_path.name, str(source_path), str(copied), "imported", None, now, now),
                )
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        return job_id, copied

    def save_draft(self, job_id: str, template: TableTemplate) -> None:
        """Atomically persist working settings, including edits before the first run."""
        folder = self.jobs_dir / job_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "working-template.json"
        pending = target.with_suffix(".tmp")
        pending.write_text(json.dumps(template.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(target)

    def load_draft(self, job_id: str) -> TableTemplate | None:
        path = self.jobs_dir / job_id / "working-template.json"
        if not path.exists():
            return None
        return TableTemplate.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_result(self, job_id: str, result: JobResult) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status = "ready" if result.unresolved_count == 0 else "review"
        self.connection.execute(
            "UPDATE jobs SET result_json = ?, status = ?, updated_at = ? WHERE id = ?",
            (json.dumps(result.to_dict(), ensure_ascii=False), status, now, job_id),
        )
        self.connection.commit()
        draft = self.load_draft(job_id)
        if draft and draft.to_dict() == result.template.to_dict():
            (self.jobs_dir / job_id / "working-template.json").unlink(missing_ok=True)

    def recent_jobs(self, limit: int = 20, *, offset: int = 0, search: str = "") -> list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT id, source_name, stored_source_path, status, updated_at FROM jobs "
            "WHERE instr(lower(source_name), lower(?)) > 0 "
            "ORDER BY updated_at DESC, created_at DESC, id DESC LIMIT ? OFFSET ?",
            (search, limit, offset),
        ).fetchall()
        return [dict(zip(("id", "source_name", "stored_source_path", "status", "updated_at"), row, strict=True)) for row in rows]

    def job_count(self, *, search: str = "") -> int:
        return self.connection.execute(
            "SELECT count(*) FROM jobs WHERE instr(lower(source_name), lower(?)) > 0", (search,),
        ).fetchone()[0]

    def load_job(self, job_id: str) -> tuple[dict[str, str], JobResult | None] | None:
        row = self.connection.execute(
            "SELECT source_name, source_path, stored_source_path, status, result_json FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return None
        metadata = dict(zip(("source_name", "source_path", "stored_source_path", "status"), row[:4], strict=True))
        result = JobResult.from_dict(json.loads(row[4])) if row[4] else None
        return metadata, result

    def save_template(self, template: TableTemplate, reference_source: str | Path | None = None) -> Path:
        if reference_source:
            source = Path(reference_source)
            if source.exists():
                destination = self.template_samples_dir / f"{template.id}{source.suffix.lower()}"
                if source.resolve() != destination.resolve():
                    shutil.copy2(source, destination)
                template.reference_source_path = str(destination)
        path = self.templates_dir / f"{template.id}.json"
        path.write_text(json.dumps(template.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_template_version(self, template: TableTemplate, reference_source: str | Path | None = None) -> TableTemplate:
        versions = [item.template_version for item in self.load_templates() if item.family_id == template.family_id]
        saved = TableTemplate.from_dict(template.to_dict())
        saved.id = str(uuid4())
        saved.family_id = template.family_id or template.id
        saved.template_version = max(versions, default=0) + 1
        saved.schema_version = max(3, saved.schema_version)
        source = reference_source or saved.reference_source_path or None
        self.save_template(saved, source)
        return saved

    def duplicate_template(self, template: TableTemplate) -> TableTemplate:
        duplicate = TableTemplate.from_dict(template.to_dict())
        duplicate.id = str(uuid4())
        duplicate.family_id = duplicate.id
        duplicate.template_version = 1
        duplicate.name = f"{template.name} — copy"
        self.save_template(duplicate, template.reference_source_path or None)
        return duplicate

    def load_templates(self) -> list[TableTemplate]:
        templates = []
        for path in sorted(self.templates_dir.glob("*.json")):
            try:
                templates.append(TableTemplate.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return templates

    def delete_template(self, template_id: str) -> None:
        path = self.templates_dir / f"{template_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sample = Path(str(data.get("reference_source_path", "")))
            if sample.parent == self.template_samples_dir and sample.exists():
                sample.unlink()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        path.unlink(missing_ok=True)

    def delete_job(self, job_id: str) -> None:
        row = self.connection.execute("SELECT stored_source_path FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row:
            shutil.rmtree(Path(row[0]).parent, ignore_errors=True)
        self.connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.connection.commit()
