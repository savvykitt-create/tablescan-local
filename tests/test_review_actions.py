import numpy as np

from tablescan_local import review_actions
from tablescan_local.domain import CellResult, FieldResult, JobResult, NormalizedRect, PageResult, TableTemplate


def test_review_order_moves_between_fields_cells_pages_and_wraps():
    t = TableTemplate('t', 't', NormalizedRect(0, 0, 1, 1), [0, 1], [0, 1])
    def cell(value):
        return CellResult(0, 0, value, value, .5, flags=['low_confidence'])
    field = FieldResult('f', 'field', '?', '?', .5, flags=['low_confidence'])
    result = JobResult('test', t, [PageResult(0, 'test', [cell('a')], [field]),
                                 PageResult(1, 'test', [cell('b')], [])])
    next_item = review_actions.next_uncertain
    assert next_item(result, 0, 'cell', -1)[:3] == (0, 'field', 0)
    assert next_item(result, 0, 'field', 0)[:3] == (0, 'cell', 0)
    assert next_item(result, 0, 'cell', 0)[:3] == (1, 'cell', 0)
    assert next_item(result, 1, 'cell', 0)[:3] == (0, 'field', 0)
    assert next_item(result, 0, 'cell', 0, page_only=True)[:3] == (0, 'field', 0)
    field.status = result.pages[0].cells[0].status = 'confirmed'
    assert next_item(result, 0, 'cell', 0, page_only=True) is None


def test_reviewing_lazy_document_renders_only_visible_pages(tmp_path, qtbot, monkeypatch):
    from PIL import Image
    from tablescan_local.documents import DocumentPages, open_document
    from tablescan_local.ui import ReviewPage
    source = tmp_path / 'review.tiff'
    frames = [Image.new('RGB', (100, 120), 'white') for _ in range(20)]
    frames[0].save(source, save_all=True, append_images=frames[1:])
    images = open_document(source)
    rendered = []
    original = DocumentPages._render
    def render(self, index):
        rendered.append(index)
        return original(self, index)
    monkeypatch.setattr(DocumentPages, '_render', render)
    t = TableTemplate('t', 't', NormalizedRect(0, 0, 1, 1), [0, 1], [0, 1])
    t.ensure_column_rules()
    pages = [PageResult(i, str(source), [CellResult(0, 0, '7', '7', .5,
                        flags=['low_confidence'], status='automatic' if i == 19 else 'confirmed')], [])
             for i in range(20)]
    view = ReviewPage()
    qtbot.addWidget(view)
    view.set_result(images, JobResult(str(source), t, pages))
    assert view.current_page == 19
    assert rendered == [0, 19]
    view.correct_value.setText('8')
    view.confirm_current()
    assert pages[19].cells[0].final_text == '8'
    assert rendered == [0, 19]
    view.clear_result()
    assert view.images == [] and view.canvas.image is None


def test_unreadable_later_page_preserves_current_review_and_selection(qtbot, monkeypatch):
    from tablescan_local.review_page import ReviewPage, QMessageBox
    class Pages:
        def __len__(self): return 2
        def __getitem__(self, index):
            if index == 1:
                raise OSError('damaged page')
            return np.full((100, 100, 3), 255, np.uint8)
    t = TableTemplate('t', 't', NormalizedRect(0, 0, 1, 1), [0, 1], [0, 1])
    t.ensure_column_rules()
    pages = [PageResult(i, 'test', [CellResult(0, 0, '7', '7', .9, status='confirmed')], [])
             for i in range(2)]
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[-1])))
    view = ReviewPage()
    qtbot.addWidget(view)
    view.set_result(Pages(), JobResult('test', t, pages))
    current_image = view.canvas.image
    view.page_select.setCurrentIndex(1)
    assert view.current_page == view.page_select.currentIndex() == 0
    assert view.canvas.image is current_image
    assert warnings == ['damaged page']
