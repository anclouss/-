from pathlib import Path
import cv2
import numpy as np
from backend.app.video_context import inspect_video


def test_sparse_real_scene_evidence_is_bounded_and_has_no_private_path(tmp_path):
    video=tmp_path/'private-input.mp4'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),12,(96,64))
    assert writer.isOpened()
    for color in ((0,0,220),(220,0,0)):
        frame=np.zeros((64,96,3),dtype=np.uint8);frame[:]=color
        for _ in range(12): writer.write(frame)
    writer.release()
    result=inspect_video(video,[{'start':0,'end':2}])
    assert result['available'] and 1<=result['samples']<=24
    assert result['scenes'] and all(0<=s['time']<=2 for s in result['scenes'])
    assert result['faces']==[]
    assert str(video) not in str(result)
    assert inspect_video(video,[{'start':0,'end':2}])==result


def test_invalid_optional_video_does_not_block_text_edits(tmp_path):
    assert inspect_video(tmp_path/'missing.mp4',[{'start':0,'end':1}])['available'] is False
    assert inspect_video(None,[])['faces']==[]
