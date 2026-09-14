import numpy as np
from tablescan_local.ocr import LocalOcrEngine


def test_short_note_in_wide_field_is_not_silently_discarded():
    image=np.full((70,2400,3),255,np.uint8)
    image[20:40,10:15]=0
    engine=object.__new__(LocalOcrEngine)
    calls=[]
    def recognize(crop, **kwargs):
        calls.append(crop.shape)
        return [([[0,0],[30,0],[30,20],[0,20]],'I',.99)],None
    engine._engine=recognize
    assert engine.recognize_region(image).text=='I'
    assert calls[0][1]<100


def test_blank_wide_field_does_not_call_ocr():
    engine=object.__new__(LocalOcrEngine)
    assert engine.recognize_region(np.full((70,2400,3),255,np.uint8)).text==''
