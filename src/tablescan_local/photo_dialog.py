"""Four-corner correction with a measured grid preview before committing."""
import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QHBoxLayout, QVBoxLayout, QMessageBox

from .document_canvas import DocumentCanvas
from .domain import TableTemplate
from .i18n import tr
from .localized_widgets import QDialog, QDialogButtonBox, QLabel, QPushButton
from .photo_geometry import find_table_corners, prepare_photo


class CornerHandle(QGraphicsEllipseItem):
    def __init__(self, point, changed):
        super().__init__(-8,-8,16,16)
        self.changed=changed
        self.setBrush(QColor('#F97316'))
        self.setPen(QPen(QColor('white'),2))
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges |
                      QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(20)
        self.setPos(point)

    def itemChange(self, change, value):
        if change==QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            bounds=self.scene().sceneRect()
            return QPointF(max(0,min(bounds.right()-1,value.x())),max(0,min(bounds.bottom()-1,value.y())))
        result=super().itemChange(change,value)
        if change==QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.changed()
        return result


class PhotoAlignmentDialog(QDialog):
    def __init__(self, image, existing=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Align photograph'))
        self.resize(1100,760)
        self.image=image
        self.result_image=self.result_transform=self.detection=None
        self.handles=[]
        layout=QVBoxLayout(self)
        note=QLabel(tr('Drag the four orange handles onto the table corners: top left, top right, bottom right, bottom left. Preview the grid, then apply. The original photo is preserved.'))
        note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout()
        self.original=DocumentCanvas();self.original.set_document(image)
        self.original.setAccessibleName(tr('Original photograph — four table corners'))
        self.preview=DocumentCanvas();self.preview.read_only=True
        self.preview.setAccessibleName(tr('Aligned photograph and detected grid'))
        row.addWidget(self.original,1);row.addWidget(self.preview,1);layout.addLayout(row,1)
        self.polygon=self.original.scene().addPolygon(QPolygonF(),QPen(QColor('#F97316'),2))
        pen=QPen(QColor('#F97316'),2);pen.setCosmetic(True);self.polygon.setPen(pen)
        corners=(existing or {}).get('corners') or find_table_corners(image) or [[.1,.2],[.9,.2],[.9,.9],[.1,.9]]
        h,w=image.shape[:2]
        for i,point in enumerate(corners):
            handle=CornerHandle(QPointF(point[0]*w,point[1]*h),self.corners_changed)
            handle.setToolTip(tr('Corner {p0}',p0=i+1));self.original.scene().addItem(handle);self.handles.append(handle)
        self.status=QLabel('');self.status.setWordWrap(True);layout.addWidget(self.status)
        actions=QHBoxLayout()
        auto=QPushButton(tr('Find corners automatically'));auto.clicked.connect(self.find_corners)
        preview=QPushButton(tr('Preview alignment'));preview.clicked.connect(self.update_preview)
        actions.addWidget(auto);actions.addWidget(preview)
        for text,factor in [('−',.8),('+',1.25)]:
            zoom=QPushButton(text);zoom.clicked.connect(lambda checked=False,f=factor: self.zoom_views(f));actions.addWidget(zoom)
        fit=QPushButton(tr('Fit preview'));fit.clicked.connect(self.fit_views);actions.addWidget(fit)
        actions.addStretch()
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject);actions.addWidget(self.buttons);layout.addLayout(actions)
        self.corners_changed()
        self.update_preview()

    def showEvent(self,event):
        super().showEvent(event)
        QTimer.singleShot(0,self.fit_views)

    def fit_views(self):
        self.original.fit_document()
        self.preview.fit_document()

    def zoom_views(self,factor):
        self.original.zoom_by(factor)
        self.preview.zoom_by(factor)

    def corners_changed(self):
        if len(self.handles)!=4:
            return
        self.polygon.setPolygon(QPolygonF([h.pos() for h in self.handles]))
        self.result_transform=None
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(False)
        self.status.setText(tr('Corners changed. Preview the alignment before applying.'))

    def find_corners(self):
        corners=find_table_corners(self.image)
        if corners is None:
            self.status.setText(tr('Automatic corners are uncertain. Drag the handles onto the table.'))
            return
        h,w=self.image.shape[:2]
        for handle,point in zip(self.handles,corners):
            handle.setPos(point[0]*w,point[1]*h)
        self.corners_changed()
        self.update_preview()

    def update_preview(self):
        h,w=self.image.shape[:2]
        corners=[[v.pos().x()/w,v.pos().y()/h] for v in self.handles]
        try:
            image,transform,detection=prepare_photo(self.image,corners=corners)
        except ValueError as exc:
            self.status.setText(str(exc));return
        self.result_image,self.result_transform,self.detection=image,transform,detection
        template=TableTemplate('preview','preview',detection.table_rect,detection.row_guides,detection.column_guides)
        self.preview.set_document(image,template)
        self.status.setText(tr('Detected {rows} rows × {columns} columns. Check every guide.',rows=template.rows,columns=template.columns)+' '+' '.join(detection.warnings))
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(True)

    def accept(self):
        if self.result_transform is not None:
            super().accept()


def edit_photo_alignment(source, template, parent=None, page=0):
    """Transactional editing: Cancel does not change the document or template."""
    import cv2
    from .documents import open_document
    from .domain import NormalizedRect
    raw=open_document(source,rotation=template.rotation_degrees)
    original=raw[page]
    old=template.page_transforms.get(page,{})
    dialog=PhotoAlignmentDialog(original,old,parent)
    if dialog.exec()!=QDialog.DialogCode.Accepted:
        return None
    result=TableTemplate.from_dict(template.to_dict())
    detection=dialog.detection
    # Reproject metadata boxes as well as the table. Source and destination
    # homographies use normalized coordinates, independent of preview size.
    def matrix(transform):
        if not transform:
            return np.eye(3)
        return cv2.getPerspectiveTransform(np.float32(transform['corners']),np.float32(transform['target']))
    mapping=matrix(dialog.result_transform) @ np.linalg.inv(matrix(old))
    for field in result.fields:
        r=field.rect
        points=np.float32([[[r.x,r.y],[r.x+r.width,r.y],[r.x+r.width,r.y+r.height],[r.x,r.y+r.height]]])
        mapped=cv2.perspectiveTransform(points,mapping)[0]
        left,top=np.clip(mapped.min(axis=0),0,1);right,bottom=np.clip(mapped.max(axis=0),0,1)
        field.rect=NormalizedRect(float(left),float(top),max(.001,float(right-left)),max(.001,float(bottom-top)))
    changes=result.resize_grid(len(detection.row_guides)-1,len(detection.column_guides)-1)
    result.row_guides=list(detection.row_guides);result.column_guides=list(detection.column_guides)
    result.table_rect=detection.table_rect
    if dialog.result_transform:
        result.page_transforms[page]=dialog.result_transform
    else:
        result.page_transforms.pop(page,None)
    result.schema_version=max(6,result.schema_version)
    result.auto_fit_rows=False  # The preview the user accepted is authoritative.
    pages=open_document(source,rotation=result.rotation_degrees,transforms=result.page_transforms)
    notices=[tr('Photograph aligned. Check the grid and metadata fields.')]+list(detection.warnings)
    if changes:
        notices.append(tr('Grid rules changed. Review their ranges before recognition.'))
    return pages,result,' '.join(notices)
