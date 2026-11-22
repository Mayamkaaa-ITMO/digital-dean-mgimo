"""Local worker. stdin/stdout carry one JPEG/result per line. No video or features are written."""
import base64
import json
import os
import sys

os.environ.update(YOLO_OFFLINE="true", YOLO_AUTOINSTALL="false", YOLO_VERBOSE="false")
from contextlib import redirect_stdout
with redirect_stdout(sys.stderr):
    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    detector = YOLO(sys.argv[1], task="detect")
    if detector.names[0] != "person":
        raise ValueError("Expected a COCO person detector")
    detector.predict(np.zeros((288,512,3), dtype=np.uint8), imgsz=512, device="cpu", verbose=False)

print('{"ready":true}', flush=True)
for packet in sys.stdin.buffer:
    try:
        raw = base64.b64decode(packet.strip(), validate=True)
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None or max(frame.shape[:2]) > 960:
            raise ValueError()
        with redirect_stdout(sys.stderr):
            result = detector.predict(frame, imgsz=512, device="cpu", classes=[0], conf=.40, max_det=30, verbose=False)[0]
        output = {"boxes": result.boxes.xyxyn.tolist()}
    except Exception:
        output = {"error":True}
    print(json.dumps(output, allow_nan=False), flush=True)
