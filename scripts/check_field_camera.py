"""Exercise the installed person model and both directions with the packaged Ultralytics sample photo."""
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
if __package__ is None:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dean.field_detector import detect, paths
from dean.field_tracking import Counter


def fixtures(folder):
    import cv2
    import numpy as np
    import ultralytics
    photo=cv2.imread(str(Path(ultralytics.__file__).parent/"assets/bus.jpg"))
    if photo is None:
        raise ValueError("В установке модели отсутствует контрольное фото bus.jpg")
    crop=cv2.resize(photo[385:910,42:215],(76,225))
    for i,x in enumerate([65,74,100,125,150,175,200,225,250,270,260,235,210,185,160,135,110,85,70,60]):
        scene=np.full((300,440,3),145,np.uint8)
        scene[38:263,x:x+76]=crop
        cv2.imwrite(str(folder/f'p{i:02}.jpg'),scene)
        scene[:]=145
        cv2.rectangle(scene,(x,38),(x+76,263),(45,72,110),-1)
        cv2.imwrite(str(folder/f'n{i:02}.jpg'),scene)


def check():
    cfg=paths()
    if not cfg:
        raise SystemExit("Детектор не установлен. Запусти prepare-camera.command.")
    with TemporaryDirectory() as tmp:
        folder=Path(tmp)
        subprocess.run([str(cfg[0]),str(Path(__file__).resolve()),"--fixtures",tmp],check=True)
        for pattern,expected in (("p*.jpg",[1,-1]),("n*.jpg",[])):
            counter=Counter("x",50,1)
            changes=[]
            for i,file in enumerate(sorted(folder.glob(pattern))):
                boxes=detect(file.read_bytes())
                changes.extend(counter.update(boxes,i*.14))
            print(json.dumps({"control":pattern,"expected":expected,"actual":changes}),flush=True)
            if changes!=expected:
                raise SystemExit("Контрольная проверка не прошла.")
    print("Модель работает локально. Контрольное фото прошло туда и обратно, движение прямоугольника проигнорировано.")


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--fixtures":
        fixtures(Path(sys.argv[2]))
    else:
        check()
