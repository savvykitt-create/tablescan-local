from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import weakref

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps
import pytest

from tablescan_local.documents import DocumentPages, open_document
from tablescan_local.imaging import rotate_document


def make_pdf(path, pages=12, width=200, height=300):
    with pdfium.PdfDocument.new() as document:
        for index in range(pages):
            page = document.new_page(width + index, height)
            page.close()
        document.save(str(path))
    return path


def test_pdf_opens_and_rotates_without_rendering_unrequested_pages(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / 'pages.pdf')
    rendered = []
    original = DocumentPages._render
    def render(self, index):
        rendered.append(index)
        return original(self, index)
    monkeypatch.setattr(DocumentPages, '_render', render)
    pages = open_document(source)
    rotated = rotate_document(pages, 90)
    assert len(rotated) == 12 and rendered == []
    assert rotated[-1].shape == (633, 900, 3)
    assert rendered == [11]
    assert rotated[11] is rotated[-1]
    assert rendered == [11]
    with pytest.raises(IndexError):
        rotated[12]


def test_full_resolution_matches_previous_pdf_rendering_exactly():
    source = Path(__file__).parents[1] / 'src/tablescan_local/default_templates/plantar.pdf'
    with pdfium.PdfDocument(str(source)) as document:
        page = document[0]
        bitmap = page.render(scale=3.0)
        try:
            expected = cv2.cvtColor(np.asarray(bitmap.to_pil().convert('RGB')), cv2.COLOR_RGB2BGR)
        finally:
            bitmap.close()
            page.close()
    pages = open_document(source)
    assert np.array_equal(pages[0], expected)
    assert np.array_equal(rotate_document(pages, 90)[0], np.rot90(expected))
    assert np.array_equal(rotate_document(rotate_document(pages, 90), -90)[0], expected)


def test_long_pdf_releases_previous_rasters_and_respects_byte_budget(tmp_path):
    pages = open_document(make_pdf(tmp_path / 'long.pdf', pages=60), scale=1,
                          cache_pages=2, cache_bytes=500_000)
    references = []
    for image in pages:
        references.append(weakref.ref(image))
        assert pages.cached_bytes <= 500_000
        assert sum(ref() is not None for ref in references) <= 2
    del image
    pages.clear_cache()
    assert pages.cached_bytes == 0 and all(ref() is None for ref in references)


def test_oversized_page_is_not_retained_in_cache(tmp_path):
    pages = open_document(make_pdf(tmp_path / 'large.pdf', pages=1), cache_bytes=100)
    image = pages[0]
    reference = weakref.ref(image)
    assert image.nbytes > 100 and pages.cached_bytes == 0
    del image
    assert reference() is None


def test_multipage_tiff_order_pixels_and_exif_are_preserved(tmp_path):
    source = tmp_path / 'frames.tiff'
    frames = [Image.fromarray(np.full((60, 90, 3), color, np.uint8))
              for color in [(220, 10, 30), (30, 180, 40), (20, 40, 190)]]
    frames[0].save(source, save_all=True, append_images=frames[1:])
    pages = open_document(source, cache_pages=1)
    assert len(pages) == 3
    with Image.open(source) as document:
        for index in (2, 0, 1):
            document.seek(index)
            expected = cv2.cvtColor(np.asarray(ImageOps.exif_transpose(document).convert('RGB')),
                                    cv2.COLOR_RGB2BGR)
            assert np.array_equal(pages[index], expected)


def test_cancelled_reader_does_not_render_or_return_cached_pages(tmp_path, monkeypatch):
    state = dict(cancelled=False)
    pages = open_document(make_pdf(tmp_path / 'cancel.pdf'), cancelled=lambda: state['cancelled'])
    pages[0]
    state['cancelled'] = True
    monkeypatch.setattr(pages, '_render', lambda _: pytest.fail('Rendered after cancellation'))
    for index in (0, 1):
        with pytest.raises(InterruptedError):
            pages[index]


def test_changed_source_cannot_mix_old_cached_and_new_pages(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (30, 40), 'white').save(source)
    pages = open_document(source)
    pages[0]
    Image.new('RGB', (50, 60), 'black').save(source)
    with pytest.raises(OSError, match='source changed'):
        pages[0]


def test_preview_resolution_is_bounded_and_parallel_readers_close_handles(tmp_path):
    source = make_pdf(tmp_path / 'preview.pdf', pages=3)
    def read(index):
        pages = open_document(source, max_dimension=100)
        image = pages[index]
        assert max(image.shape[:2]) <= 100
        return image.shape
    with ThreadPoolExecutor(max_workers=2) as workers:
        shapes = list(workers.map(read, range(3)))
    assert len(shapes) == 3
    # Also works on Windows: no native reader keeps the file open.
    source.rename(tmp_path / 'moved.pdf')


def test_preflight_checks_all_orientations_with_one_full_render_per_page(tmp_path, monkeypatch):
    from tablescan_local.batch_preflight import prepare_file
    source = make_pdf(tmp_path / 'preflight.pdf', pages=4)
    rendered = []
    original = DocumentPages._render
    def render(self, index):
        rendered.append((self.max_dimension, index))
        return original(self, index)
    monkeypatch.setattr(DocumentPages, '_render', render)
    item = prepare_file(source, [])
    assert not item.error and item.pages == 4
    assert rendered == [(None, index) for index in range(4)]
    assert {r: len(pages) for r, pages in item.geometry.items()} == {r: 4 for r in (0, 90, 180, 270)}
    assert isinstance(item.thumbnails, DocumentPages)
    assert item.thumbnails.cached_bytes == 0
    assert max(item.thumbnails[3].shape[:2]) <= 1000
