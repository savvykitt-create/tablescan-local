"""Shared preparation for imports, batch checks and manual alignment."""
from functools import lru_cache
from pathlib import Path
from threading import Lock
import re

import cv2
import numpy as np

from .documents import open_document
from .imaging import detect_grid
from .photo_geometry import prepare_photo

_TITLE_LOCK = Lock()


@lru_cache(maxsize=1)
def _title_engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(rec_model_path=str(Path(__file__).parent/'models/en_PP-OCRv5_rec_mobile.onnx'),
                    intra_op_num_threads=1, inter_op_num_threads=1)


def read_printed_title(image, detection):
    """Only the band above the grid; local bundled OCR, never handwriting values."""
    h,w=image.shape[:2]
    top=round(detection.row_guides[0]*h)
    if top<20:
        return ''
    x1=max(0,round(detection.column_guides[0]*w)-20)
    x2=min(w,round(detection.column_guides[-1]*w)+20)
    crop=image[max(0,top-round(h*.25)):max(1,top-4),x1:x2]
    if crop.size==0:
        return ''
    if crop.shape[1]>1400:
        crop=cv2.resize(crop,None,fx=1400/crop.shape[1],fy=1400/crop.shape[1],interpolation=cv2.INTER_AREA)
    try:
        with _TITLE_LOCK:
            result,_=_title_engine()(crop,use_cls=False)
        return ' '.join(str(row[1]) for row in (result or []) if float(row[2])>=.75)
    except Exception:
        # Missing or unreadable titles never become a guessed protocol.
        return ''


def title_tokens(text):
    return ' '.join(re.findall(r'[^\W_]+',text.casefold(),flags=re.UNICODE))


def title_matches(template, text):
    observed=' '+title_tokens(text)+' '
    labels=[template.name]
    labels += [f.fixed_value for f in template.fields if f.source=='fixed' and f.name.casefold() in ('assay','protocol','test')]
    return any(len(title_tokens(label))>=4 and ' '+title_tokens(label)+' ' in observed for label in labels)


def prepare_page(image, *, photograph=True, title=False):
    if photograph:
        try:
            prepared,transform,detection=prepare_photo(image)
        except ValueError:
            prepared,transform,detection=image,{},detect_grid(image)
    else:
        prepared,transform,detection=image,{},detect_grid(image)
    detection.transform=transform
    if title:
        detection.title_text=read_printed_title(prepared,detection)
    return prepared,detection


def prepare_document(path, templates=(), *, rotation=None):
    """Choose orientation once, then persist per-page transforms for lazy readers."""
    from .template_matcher import rank_templates
    raw=open_document(path)
    photograph=Path(path).suffix.lower()!='.pdf'
    rotations=[rotation] if rotation is not None else ([0,90,180,270] if photograph else [0])
    options=[]
    original=raw[0]
    for angle in rotations:
        image=np.ascontiguousarray(np.rot90(original,angle//90)) if angle else original
        try:
            prepared,detection=prepare_page(image,photograph=photograph,title=bool(templates))
        except ValueError:
            continue
        matches=rank_templates(prepared.shape,detection,list(templates))
        score=matches[0].score if matches else min(len(detection.row_guides),len(detection.column_guides))/100
        options.append((score,angle,detection))
    if not options:
        raise ValueError('No reliable table boundary was found. Use Align photograph to mark the four corners.')
    options.sort(key=lambda entry:(entry[0],entry[1]==0),reverse=True)
    _,angle,first=options[0]
    transforms={0:first.transform} if first.transform else {}
    for index in range(1,len(raw)):
        image=np.ascontiguousarray(np.rot90(raw[index],angle//90))
        try:
            _,detection=prepare_page(image,photograph=photograph)
        except ValueError:
            continue
        if detection.transform:
            transforms[index]=detection.transform
    return open_document(path,rotation=angle,transforms=transforms), first
