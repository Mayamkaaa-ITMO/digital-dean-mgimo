"""Local optical recognition of uploaded timetable images."""
from pathlib import Path
import json
import os
import re
import subprocess
import shutil
import sys
import csv
from io import StringIO
from .db import ROOT
from .domain import DomainError

DAYS = ("понедельник","вторник","среда","четверг","пятница","суббота","воскресенье")
SHORT_DAYS = ("пн","вт","ср","чт","пт","сб","вс")
DAY_AT_START = re.compile(r"^\s*("+"|".join((*DAYS,*SHORT_DAYS))+r")(?:\.|\b)",re.I)
CLOCK = r"(?:[0-2]\s*)?\d\s*[:.]\s*\d\s*\d"
TIMES = re.compile(rf"(?<!\d)({CLOCK})\s*[—–−-]\s*({CLOCK})(?!\d)")
KINDS = {"лекция","семинар","практика","лабораторная","экзамен"}


def lab_mode():
    return os.getenv("DEAN_CAMPUS_LAB") == "1"


def parse_boxes(boxes):
    lines=[]
    for box in sorted(boxes,key=lambda b:(b["y"],b["x"])):
        if not lines or abs(lines[-1][0]["y"]-box["y"]) > .58*max(lines[-1][0]["h"],box["h"]):
            lines.append([])
        lines[-1].append(box)
    events=[]
    day=None
    for line in lines:
        text=" ".join(b["text"].strip() for b in sorted(line,key=lambda b:b["x"]))
        heading=DAY_AT_START.match(text)
        if heading:
            token=heading[1].casefold()
            day=(DAYS if token in DAYS else SHORT_DAYS).index(token)+1
        match=TIMES.search(text)
        if not match or not day:
            continue
        tail=text[match.end():].strip()
        room=re.search(r"(?<!\w)(?:ауд(?:итория|\.)?\s*[:№]?\s*)?((?:[А-ЯA-Z][- ]?)?\d{2,4}[а-яa-z]?)\s*$",tail,re.I)
        room_name=room[1].strip() if room else ""
        if room:tail=tail[:room.start()].strip()
        kind=next((k for k in KINDS if re.search(r"\b"+k+r"\s*$",tail,re.I)),"")
        subject=tail[:-len(kind)].strip() if kind else tail
        start,end = ("".join(value.split()).replace(".",":").zfill(5) for value in match.groups())
        events.append(dict(day=day,start=start,end=end,
                           subject=subject,room=room_name,kind=kind,uncertain=True))
    return {"events":events}


def local(body):
    binary=ROOT/".runtime/campus-ocr"
    if binary.is_file():
        try:
            probe=subprocess.run([str(binary)],input=body,capture_output=True,timeout=25,check=True)
            result=parse_boxes(json.loads(probe.stdout))
            if result["events"]:
                return result,"Распознано на Mac · Apple Vision"
        except (subprocess.SubprocessError,OSError,ValueError,TypeError,KeyError):
            pass
    if shutil.which("tesseract"):
        try:
            completed = subprocess.run(["tesseract","stdin","stdout","-l","rus+eng","--psm","6","tsv"],
                                       input=body,capture_output=True,timeout=25,check=True)
            words = csv.DictReader(StringIO(completed.stdout.decode()),delimiter="\t",quoting=csv.QUOTE_NONE)
            boxes = [{"text":r["text"], "x":int(r["left"]), "y":int(r["top"])+int(r["height"])/2,
                      "h":int(r["height"])} for r in words if r.get("level")=="5" and r["text"].strip()]
            result = parse_boxes(boxes)
            if result["events"]:
                return result,"Локальный Tesseract · сверь строки со снимком"
        except (subprocess.SubprocessError,OSError,UnicodeError,ValueError,TypeError,KeyError):
            pass
    try:
        runner = os.environ.get("DEAN_OCR_PYTHON") or sys.executable
        printed = subprocess.run([runner, str(ROOT/"scripts/campus_print_ocr.py")],
                                 input=body, capture_output=True, timeout=20, check=True)
        result = parse_boxes(json.loads(printed.stdout))
        if result["events"]:
            return result, "Локальное распознавание печатных строк · сверь со снимком"
    except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError):
        pass
    raise DomainError(422,"На снимке не удалось прочитать пары. Загрузи более чёткое фото с днями и временем.")
