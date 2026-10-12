import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from tablescan_local.documents import open_document
from tablescan_local.domain import TableTemplate
from tablescan_local.imaging import detect_grid, write_image, GridDetection
from tablescan_local.photo_geometry import apply_transform, corner_transform, find_table_corners, prepare_photo
from tablescan_local.photo_preparation import prepare_document
from tablescan_local.template_matcher import rank_templates


def form(rows=21):
    image=np.full((900,1400,3),245,np.uint8)
    xs=np.r_[100,160,np.linspace(260,1160,10),1300]
    ys=np.linspace(200,800,rows+1)
    for x in xs:
        cv2.line(image,(round(x),200),(round(x),800),(40,40,40),2)
    for y in ys:
        cv2.line(image,(100,round(y)),(1300,round(y)),(40,40,40),2)
    cv2.putText(image,'Von Frey',(100,100),cv2.FONT_HERSHEY_SIMPLEX,1,(20,20,20),2)
    for y in ys[1:]:
        for x in xs[1:-2]:
            cv2.putText(image,'32.5',(round(x)+8,round(y)-6),cv2.FONT_HERSHEY_SIMPLEX,.4,(150,60,40),1)
    return image


def photograph(rows=21, tilt=1):
    image=form(rows)
    src=np.float32([[100,200],[1300,200],[1300,800],[100,800]])
    dst=src+np.float32([[18,0],[-5,22],[12,-8],[-8,0]])*tilt
    matrix=cv2.getPerspectiveTransform(src,dst)
    result=cv2.warpPerspective(image,matrix,(1400,900),borderValue=(245,245,245))
    shadow=np.linspace(.70,1.,900)[:,None,None]
    return np.uint8(result*shadow)


@pytest.mark.parametrize('rows,tilt',[(10,1),(21,1),(30,1),(21,2)])
def test_automatic_corners_recover_complete_photographed_grid(rows,tilt):
    original=photograph(rows,tilt)
    before=original.copy()
    prepared,transform,detection=prepare_photo(original)
    assert transform
    assert len(detection.row_guides)-1==rows
    assert len(detection.column_guides)-1==12
    assert not detection.warnings
    np.testing.assert_array_equal(original,before)
    np.testing.assert_array_equal(prepared,apply_transform(original,json.loads(json.dumps(transform))))


def test_straight_form_does_not_get_resampled():
    original=form()
    prepared,transform,_=prepare_photo(original)
    assert not transform
    np.testing.assert_array_equal(original,prepared)


def test_page_edges_without_lattice_are_not_a_table():
    image=np.full((900,1400,3),255,np.uint8)
    cv2.rectangle(image,(50,50),(1350,850),(0,0,0),3)
    assert find_table_corners(image) is None
    assert detect_grid(image).warnings


@pytest.mark.parametrize('points',[
    [[.1,.1],[.9,.9],[.9,.1],[.1,.9]],
    [[.1,.1],[.1,.1],[.1,.1],[.1,.1]],
    [[-.1,.1],[.9,.1],[.9,.9],[.1,.9]],
    [[float('nan'),.1],[.9,.1],[.9,.9],[.1,.9]],
])
def test_invalid_corners_are_rejected(points):
    with pytest.raises(ValueError):
        corner_transform(points,(900,1400,3))


def test_lazy_pages_and_template_roundtrip_reproduce_geometry(tmp_path):
    image=photograph()
    prepared,transform,detection=prepare_photo(image)
    path=tmp_path/'photo.png';write_image(path,image)
    t=TableTemplate('test','test',detection.table_rect,detection.row_guides,detection.column_guides,page_transforms={0:transform})
    restored=TableTemplate.from_dict(json.loads(json.dumps(t.to_dict())))
    pages=open_document(path,transforms=restored.page_transforms)
    np.testing.assert_array_equal(pages[0],prepared)
    assert pages.rendering['transforms']==restored.page_transforms
    assert len(pages)==1
    # Rotating means starting again from original pixels, without stale warps.
    assert pages.rotated(90).transforms=={}


def test_title_resolves_identical_grids_without_overriding_bad_geometry():
    d=detect_grid(form())
    templates=[TableTemplate(name,name,d.table_rect,d.row_guides,d.column_guides) for name in ('Plantar','Von Frey')]
    d.title_text='Von Frey Date 25.09.26'
    matches=rank_templates(form().shape,d,templates)
    assert matches[0].template.name=='Von Frey'
    assert matches[0].score-matches[1].score>.2
    d.warnings=['uncertain grid']
    assert rank_templates(form().shape,d,templates)[0].score<.55


def test_missing_title_explains_ambiguous_protocol():
    d=detect_grid(form())
    templates=[TableTemplate(name,name,d.table_rect,d.row_guides,d.column_guides) for name in ('Plantar','Von Frey')]
    matches=rank_templates(form().shape,d,templates)
    assert matches[0].score==matches[1].score
    assert any('confirm' in str(r).lower() for r in matches[0].reasons)


def test_document_orientation_uses_printed_title_and_persists_warp(tmp_path,monkeypatch):
    from tablescan_local import photo_preparation
    image=photograph()
    path=tmp_path/'sideways.png';write_image(path,np.rot90(image,-1))
    d=detect_grid(form());t=TableTemplate('t','Von Frey',d.table_rect,d.row_guides,d.column_guides)
    monkeypatch.setattr(photo_preparation,'read_printed_title',lambda image,d:'Von Frey' if d.table_rect.y<.4 and image.shape[1]>image.shape[0] else '')
    pages,d=prepare_document(path,[t])
    assert pages.rotation==90
    assert pages.transforms
    assert len(d.row_guides)==22 and len(d.column_guides)==13


def test_four_corner_dialog_requires_preview_after_drag_and_cancel_is_safe(qtbot):
    from tablescan_local.photo_dialog import PhotoAlignmentDialog
    from PySide6.QtWidgets import QDialogButtonBox
    image=photograph();original=image.copy()
    dialog=PhotoAlignmentDialog(image);qtbot.addWidget(dialog)
    assert dialog.detection and dialog.result_transform
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    dialog.handles[0].moveBy(2,1)
    assert not dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    dialog.update_preview()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    dialog.reject()
    np.testing.assert_array_equal(image,original)


def test_worker_uses_same_transformed_pixels_as_preview(tmp_path,monkeypatch):
    from tablescan_local import recognition_worker
    image=photograph();prepared,transform,d=prepare_photo(image)
    path=tmp_path/'photo.png';write_image(path,image)
    t=TableTemplate('t','t',d.table_rect,d.row_guides,d.column_guides,page_transforms={0:transform})
    received=[]
    def process(images,*args,**kwargs):
        received.append(images[0].copy())
        return object()
    monkeypatch.setattr(recognition_worker,'process_document',process)
    worker=recognition_worker.RecognitionWorker(None,str(path),t,tmp_path/'crops')
    worker.run()
    assert len(received)==1
    np.testing.assert_array_equal(received[0],prepared)


def test_batch_assessment_retains_warp_and_disambiguates_title():
    from tablescan_local.batch_preflight import assess_geometry
    image=photograph();prepared,transform,d=prepare_photo(image)
    d.transform=transform;d.title_text='Von Frey'
    templates=[TableTemplate(name,name,d.table_rect,d.row_guides,d.column_guides) for name in ('Plantar','Von Frey')]
    assessments=[assess_geometry(t,[(image.shape,d)],all_templates=templates) for t in templates]
    assert assessments[1].score>assessments[0].score+20
    assert assessments[1].template.page_transforms=={0:transform}
    assert not assessments[1].template.auto_fit_rows


def test_residual_bend_correction_straightens_observed_rule():
    from tablescan_local.photo_geometry import refine_curvature
    image=form();h,w=image.shape[:2]
    x,y=np.meshgrid(np.arange(w,dtype=np.float32),np.arange(h,dtype=np.float32))
    curved=cv2.remap(image,x,y+4*np.sin(np.pi*(x-100)/1200),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    d=detect_grid(curved)
    identity=corner_transform([[.1,.1],[.9,.1],[.9,.9],[.1,.9]],image.shape)
    refined=refine_curvature(curved,d,identity)
    assert 'mesh' in refined
    corrected=apply_transform(curved,refined)
    center=round(d.row_guides[10]*h)
    def variation(im):
        hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
        neutral=cv2.inRange(hsv,np.array([0,0,0]),np.array([179,105,205]))
        heights=[]
        for xx in range(180,1250,90):
            projection=np.mean(neutral[center-10:center+11,xx-20:xx+20]>0,axis=1)
            heights.append(np.argmax(projection))
        return np.std(heights)
    assert variation(corrected)<variation(curved)*.65


def test_phone_job_prepares_before_template_choice_and_reopens_same_pixels(tmp_path,monkeypatch,qtbot):
    from tablescan_local import ui, photo_preparation
    from PySide6.QtWidgets import QDialog
    monkeypatch.setenv('TABLESCAN_DATA_DIR',str(tmp_path/'data'))
    image=photograph();path=tmp_path/'phone.png';write_image(path,image)
    prepared,transform,d=prepare_photo(image)
    d.title_text='Von Frey'
    pages=open_document(path,transforms={0:transform})
    template=TableTemplate('von','Von Frey',d.table_rect,d.row_guides,d.column_guides,auto_fit_rows=True)
    _,window=ui.create_application();qtbot.addWidget(window)
    monkeypatch.setattr(window.store,'load_templates',lambda:[template])
    monkeypatch.setattr(photo_preparation,'prepare_document',lambda *a,**k:(pages,d))
    selected=[]
    def choose(dialog):
        selected.append(dialog.list.count())
        dialog.list.setCurrentRow(1)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui.TemplateChoiceDialog,'exec',choose)
    job,_=window.store.import_source(path)
    window.open_recent(job)
    assert selected and window.template.name=='Von Frey'
    assert window.template.page_transforms=={0:transform}
    np.testing.assert_array_equal(window.images[0],prepared)
    window.open_recent(job)
    assert len(selected)==1
    np.testing.assert_array_equal(window.images[0],prepared)
    assert window.store.job_count()==1
