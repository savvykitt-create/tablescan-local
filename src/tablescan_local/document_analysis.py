"""Document orchestration without Qt: bounded page access and resumable stages."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import time

import numpy as np

from . import checkpoints
from .domain import JobResult
from .documents import DocumentPages
from .i18n import tr
from .table_checks import flag_table_outliers
from .template_fit import fit_document_template


def _checkpoint(directory, run_id, images, source_path, template, high_accuracy, slow_mode, cancelled, adaptive=False):
    if directory is None:
        return None
    if not run_id:
        raise ValueError('Resumable analysis requires a run identity')
    if isinstance(images, DocumentPages):
        source = dict(sha256=checkpoints.file_digest(images.path, cancelled), rendering=images.rendering)
    else:
        source = []
        for image in images:
            checkpoints.check_cancelled(cancelled)
            source.append(dict(shape=image.shape, dtype=str(image.dtype),
                               sha256=hashlib.sha256(np.ascontiguousarray(image)).hexdigest()))
    from .adaptive_ocr import POLICY
    identity = dict(run_id=run_id, source=source, template=template.to_dict(),
                    adaptive_policy=POLICY if adaptive else None,
                    recognition=checkpoints.recognition_identity(high_accuracy, slow_mode, cancelled))
    return checkpoints.AnalysisCheckpoint(directory, identity, template, source_path)


def analyze_document(images, source_path, template, recognize_page, engine_factory, *,
                     progress=None, crop_root=None, high_accuracy=True, slow_mode=False,
                     checkpoint_directory=None, run_id=None, cancelled=lambda: False, adaptive=False):
    high_accuracy = bool(high_accuracy or adaptive)
    started = time.monotonic()
    checkpoints.check_cancelled(cancelled)
    if not images:
        raise ValueError(tr('The document contains no pages.'))
    if progress:
        progress(0, 1, tr('Preparing analysis'))
    template = fit_document_template(template, images, cancelled)
    template.ensure_column_rules()
    template.validate_value_rules()
    checkpoint = _checkpoint(checkpoint_directory, run_id, images, source_path, template,
                             high_accuracy, slow_mode, cancelled, adaptive)
    excluded = template.exclusion_mask()
    needs_ocr = (any(field.source != 'fixed' for field in template.fields)
                 or any(template.fixed_value(r, c) is None and (r, c) not in excluded
                        for r in range(template.rows) for c in range(template.columns)))
    from .resource_policy import resources, cpu_workers
    from .parallel_ocr import CellWorkers
    from .ocr_runtime import acceleration_provider
    from .numeric_decoder import _native_search
    from .ocr import LocalOcrEngine as NativeEngine
    state = resources()
    engine, pool = None, None
    model_version = 'template-values'
    performance = dict(resources=state.to_dict(), cpu_workers=1,
                       ocr_provider_available=(acceleration_provider() or ('CPUExecutionProvider',))[0],
                       decoder='native' if _native_search else 'python', primary_pages_seconds=[])
    resumed = dict(primary_pages=[], slow_pages=[], saved_primary_seconds=0.0, saved_slow_seconds=0.0)
    page_count = len(images)
    total_steps = page_count * (2 if slow_mode else 1) * 1000

    def report(index, stage):
        def emit(value, maximum, label):
            checkpoints.check_cancelled(cancelled)
            if progress:
                position = (stage * page_count + index) * 1000
                position += round(1000 * max(0, min(1, value / max(1, maximum))))
                progress(position, total_steps,
                         tr('Page {p0} of {p1}: {p2}', p0=index + 1, p1=page_count, p2=label))
        return emit

    pages = []
    try:
        for index in range(page_count):
            checkpoints.check_cancelled(cancelled)
            saved = checkpoint.load('primary', index) if checkpoint else None
            if saved:
                pages.append(saved.page)
                model_version = saved.model_version
                resumed['primary_pages'].append(index + 1)
                resumed['saved_primary_seconds'] += saved.seconds
                performance['primary_pages_seconds'].append(0.0)
                report(index, 0)(1, 1, tr('Restored completed recognition'))
                continue
            if needs_ocr and engine is None:
                engine = engine_factory(high_accuracy=high_accuracy or slow_mode)
                model_version = engine.model_version
                # External/test engines retain their own recognition semantics.
                if isinstance(engine, NativeEngine) and (high_accuracy or slow_mode):
                    cells = sum(template.fixed_value(r, c) is None and (r, c) not in excluded
                                for r in range(template.rows) for c in range(template.columns)) * (page_count - index)
                    count = cpu_workers(state, cells, accelerated=acceleration_provider() is not None)
                    performance['cpu_workers'] = count
                    if count > 1:
                        pool = CellWorkers(count)
                        engine._cell_workers = pool
            page_started = time.monotonic()
            directory = crop_root / f'page-{index + 1}' if crop_root else None
            page = recognize_page(images[index], source_path, index, template, engine,
                                  report(index, 0), directory, **({"adaptive": True} if adaptive else {}))
            seconds = time.monotonic() - page_started
            if checkpoint:
                checkpoint.save('primary', page, seconds, model_version)
            pages.append(page)
            performance['primary_pages_seconds'].append(seconds)
    finally:
        if pool:
            performance.update(cpu_fallbacks=pool.fallbacks, cpu_parallel_cells=pool.completed,
                               cpu_worker_reductions=pool.reductions, cpu_worker_events=list(pool.events))
            pool.close()
        # Never retain ONNX sessions while the larger Slow models are loaded.
        engine = None
        if isinstance(images, DocumentPages):
            images.clear_cache()
    if slow_mode:
        import gc
        from .slow_mode import refine_page, model_session, runtime_config, eligible_cells
        gc.collect()
        try:
            slow_config = runtime_config() if any(eligible_cells(page, template) for page in pages) else {}
        except RuntimeError as exc:
            slow_config = {'unavailable_error': str(exc)}
        with model_session():
            for index, page in enumerate(pages):
                checkpoints.check_cancelled(cancelled)
                baseline = checkpoints.json_digest(page.to_dict())
                saved = checkpoint.load('slow', index, baseline=baseline) if checkpoint else None
                if saved:
                    pages[index] = saved.page
                    resumed['slow_pages'].append(index + 1)
                    resumed['saved_slow_seconds'] += saved.seconds
                    report(index, 1)(1, 1, tr('Restored completed verification'))
                    continue
                report(index, 1)(0, 1, tr('Preparing Slow verification'))
                page_started = time.monotonic()
                page_root = crop_root / f'page-{index + 1}' if crop_root else None
                directory = (page_root or Path(tempfile.mkdtemp(prefix='tablescan-slow-'))) / 'slow-mode'
                refine_page(images[index], page, template, directory, slow_config, report(index, 1))
                flag_table_outliers(page, template)
                if checkpoint:
                    checkpoint.save('slow', page, time.monotonic() - page_started,
                                    model_version, baseline=baseline)
    checkpoints.check_cancelled(cancelled)
    if isinstance(images, DocumentPages):
        images.check_unchanged()
        images.clear_cache()
    if adaptive:
        from .adaptive_ocr import POLICY
        performance['adaptive'] = dict(policy=POLICY,
            short_cells=sum('adaptive_short_pass' in c.flags for p in pages for c in p.cells),
            full_cells=sum('adaptive_full_pass' in c.flags for p in pages for c in p.cells))
        model_version += '+adaptive-' + POLICY
    performance['total_seconds'] = time.monotonic() - started
    if checkpoint:
        performance['checkpoint'] = resumed
    if crop_root:
        crop_root.mkdir(parents=True, exist_ok=True)
        checkpoints.atomic_json(crop_root / 'performance.json', performance)
    return JobResult(source_path, template, pages,
                     model_version + ('+slow-qwen-glm-v1' if slow_mode else ''), performance)
