"""Qt worker boundary for document analysis; no dependency on application widgets."""
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from .documents import DocumentPages, open_document
from .domain import TableTemplate
from .i18n import tr
from .pipeline import process_document


class RecognitionWorker(QThread):
    progress = Signal(int, int, object)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, images: Sequence[np.ndarray] | None, source_path: str, template: TableTemplate, crop_root: Path, high_accuracy: bool = True, parent: QObject | None = None, slow_mode: bool = False, *, auto_prepare: bool = False, run_id: str | None = None, adaptive: bool = False) -> None:
        super().__init__(parent)
        self.images = images
        self.source_path = source_path
        self.template = template
        self.crop_root = crop_root
        self.high_accuracy = bool(high_accuracy or adaptive)
        self.adaptive = adaptive
        self.slow_mode = slow_mode
        self.auto_prepare = auto_prepare
        self.preparation = None
        self.run_id = run_id or str(uuid4())

    def run(self) -> None:
        try:
            if self.auto_prepare:
                from .batch_preflight import prepare_file
                checked = prepare_file(self.source_path, [self.template], self.isInterruptionRequested,
                                       lambda message: self.progress.emit(0, 1, message))
                assessment = checked.assessments.get(self.template.id)
                if checked.error or not assessment or assessment.template is None:
                    reason = checked.error or (assessment.error if assessment else '')
                    raise ValueError(str(tr('Alignment required. Use Prepare files again to correct the protocol before retrying.')) + ' ' + reason)
                self.template = assessment.template
                self.preparation = dict(rotation=assessment.rotation_degrees, status=assessment.fit_status,
                                        score=assessment.score, rows=self.template.rows, columns=self.template.columns)
            if self.images is None:
                self.images = open_document(self.source_path, rotation=self.template.rotation_degrees,
                                            cache_pages=1, cancelled=self.isInterruptionRequested,
                                            transforms=self.template.page_transforms)
            if self.isInterruptionRequested():
                raise InterruptedError
            def report(value: int, maximum: int, label: str) -> None:
                if self.isInterruptionRequested():
                    raise InterruptedError
                self.progress.emit(value, maximum, label)

            result = process_document(
                self.images, self.source_path, self.template,
                report, self.crop_root, self.high_accuracy, slow_mode=self.slow_mode,
                checkpoint_directory=self.crop_root.parent / 'analysis-checkpoint',
                run_id=self.run_id, cancelled=self.isInterruptionRequested,
                **({"adaptive": True} if self.adaptive else {}),
            )
            self.completed.emit(result)
        except InterruptedError:
            self.cancelled.emit()
        except Exception as exc:  # UI boundary: surface actionable error
            self.failed.emit(str(exc))

        finally:
            if isinstance(self.images, DocumentPages):
                self.images.clear_cache()
            self.images = None
