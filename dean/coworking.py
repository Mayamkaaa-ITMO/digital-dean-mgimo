"""Single-door on-site measurements. All images are processed in RAM on the station."""
import base64
import binascii
import csv
import io
import json
import math
import re
import threading
import time
from datetime import datetime
from os import getenv
from urllib.parse import urlsplit

from . import campus_lab, field_detector, services
from .clock import now
from .db import write
from .domain import DomainError, field, identifier, require, role, timestamp
from .field_tracking import Counter

PATH = "/coworking"
KINDS = {"field":"Замер на месте", "practice":"Репетиция"}
ACTIONS = {"start":"Начальный пересчёт", "enter":"Вход", "exit":"Выход", "pause":"Камера остановлена",
           "resume":"Камера подключена", "recount":"Ручная сверка", "end":"Замер завершён", "gap":"Пропуск наблюдения"}
LOCK = threading.Lock()
tracks = {}


def allowed(request, staff=False):
    campus_lab.access(request.user)
    if staff:
        role(request.user,"staff")


def station(request):
    allowed(request, True)
    if (getattr(request,"product_design",False) and getattr(request,"secure",False)
            and (request.origin or "").startswith("https://") and getenv("DEAN_FIELD_REMOTE") == "1"):
        return
    if (urlsplit(request.origin or "").hostname not in {"localhost","127.0.0.1","::1"} or
        request.env.get("REMOTE_ADDR") not in {"127.0.0.1","::1"}):
        raise DomainError(403,"Включи камеру через localhost или настрой защищённый приём на сервере.")


def session(db, request, sid):
    return require(db.execute("SELECT * FROM field_session WHERE id=? AND university_id=?",
                              (sid, request.user["university_id"])).fetchone())


def whole(value, low, high, label):
    if not re.fullmatch(r"[0-9]{1,6}", value) or not low <= int(value) <= high:
        raise DomainError(422,f"{label} — число от {low} до {high}.")
    return int(value)


def geofence(request):
    a,b = request.field("latitude").strip(),request.field("longitude").strip()
    if not a and not b:
        return None,None
    try:
        lat,lon = float(a),float(b)
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat,lon
    except ValueError:
        pass
    raise DomainError(422,"Проверь обе координаты центра кампуса.")


def event(db,sid,at,kind,delta,occupancy):
    db.execute("INSERT INTO field_event(session_id,at,kind,delta,occupancy) VALUES (?,?,?,?,?)",
               (sid,at,kind,delta,occupancy))


def create(request,db):
    allowed(request,True)
    room = field(request.field("room"),"Название",80)
    kind, axis, inside = (request.field(k) for k in ("kind","axis","inside"))
    if kind not in KINDS or axis not in {"x","y"} or inside not in {"1","-1"}:
        raise DomainError(422,"Проверь тип замера и направление входа.")
    capacity = whole(request.field("capacity"),1,10000,"Вместимость")
    initial = whole(request.field("initial"),0,10000,"Людей внутри")
    line = whole(request.field("line"),20,80,"Положение линии")
    start=whole(request.field("gate_start") or "0",0,99,"Начало прохода")
    end=whole(request.field("gate_end") or "100",1,100,"Конец прохода")
    if end-start<5:
        raise DomainError(422,"Выдели дверной проём длиной хотя бы 5% кадра.")
    coords = geofence(request)
    radius = whole(request.field("radius") or "150",10,2000,"Радиус в метрах")
    args = [request.user["university_id"],room,kind,capacity,initial,initial,axis,line,int(inside),*coords,radius,start,end]
    def save():
        sid,at = identifier(),timestamp()
        db.execute("""INSERT INTO field_session(id,university_id,room,kind,capacity,initial_count,occupancy,
                   axis,line,inside,campus_lat,campus_lon,radius,gate_start,gate_end,started) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (sid,*args,at))
        event(db,sid,at,"start",0,initial)
        return sid
    sid=services.command(db,request.user,"field.create",request.field("request_id"),args,save)
    return 303,b"",[("Location",PATH+"/"+sid)]


def age(stamp):
    return (now()-datetime.fromisoformat(stamp)).total_seconds() if stamp else math.inf


def packet(data):
    return 200,json.dumps(data,ensure_ascii=False),[("Content-Type","application/json; charset=utf-8"),("Cache-Control","no-store")]


def current(db,row):
    totals=dict(db.execute("SELECT kind,count(*) FROM field_event WHERE session_id=? GROUP BY kind",(row["id"],)))
    journal=[{"at":r[0],"kind":ACTIONS[r[1]],"occupancy":r[2]} for r in db.execute(
        "SELECT at,kind,occupancy FROM field_event WHERE session_id=? ORDER BY id DESC LIMIT 16",(row["id"],))]
    condition = ("Завершён" if row["ended"] else "Камера выключена" if not row["camera"]
                 else "Ожидание кадров" if not row["frame_no"] or age(row["last_frame"])>4 else "Идёт подсчёт")
    heat = [dict(r) for r in db.execute("SELECT minute,low,high,frames FROM field_minute WHERE session_id=? ORDER BY minute DESC LIMIT 15",(row["id"],))]
    return {"heat":heat[::-1],"occupancy":row["occupancy"],"capacity":row["capacity"],"initial":row["initial_count"],
            "enter":totals.get("enter",0),"exit":totals.get("exit",0),"condition":condition,
            "check":bool(row["discrepancy"] or (row["camera"] and age(row["last_frame"])>6)),
            "ended":bool(row["ended"]),"last":row["last_frame"],"journal":journal}


def status(request,db,sid):
    allowed(request)
    return packet(current(db,session(db,request,sid)))


def camera_key(request):
    key=request.field("camera")
    if not re.fullmatch("[a-f0-9]{32}",key):
        raise DomainError(422,"Перезапусти камеру.")
    return key


def claim(request,db,sid):
    station(request)
    key=camera_key(request)
    with LOCK,write(db):
        row=session(db,request,sid)
        if row["ended"]:
            raise DomainError(409,"Этот замер завершён.")
        if row["camera"]==key:
            return packet({"ready":True,"seq":row["frame_no"]})
        if row["camera"] and age(row["last_frame"])<15:
            raise DomainError(409,"Камера уже подключена в другой вкладке.")
        at=timestamp()
        if row["camera"]:
            event(db,sid,at,"gap",0,row["occupancy"])
            db.execute("UPDATE field_session SET discrepancy=1 WHERE id=?",(sid,))
        db.execute("UPDATE field_session SET camera=?,frame_no=0,last_frame=? WHERE id=?",(key,at,sid))
        event(db,sid,at,"resume",0,row["occupancy"])
        tracks.pop(sid,None)
    return packet({"ready":True,"seq":0})


def decode_frame(value):
    try:
        raw=base64.b64decode(value,validate=True)
        if not 50 <= len(raw) <= 290000 or raw[:2]!=b"\xff\xd8":
            raise ValueError
        # Bound pixel dimensions before invoking any image decoder.
        i=2
        while i+9<len(raw) and raw[i]==255:
            marker=raw[i+1]
            if marker in (192,193,194):
                h,w=int.from_bytes(raw[i+5:i+7],"big"),int.from_bytes(raw[i+7:i+9],"big")
                if min(w,h)>=32 and max(w,h)<=960:
                    return raw
                break
            size=int.from_bytes(raw[i+2:i+4],"big")
            if size<2:
                break
            i+=2+size
    except (ValueError,binascii.Error):
        pass
    raise DomainError(422,"Камера прислала нечитаемый кадр.")


def frame(request,db,sid):
    station(request)
    key=camera_key(request)
    seq=whole(request.field("seq"),1,999999,"Номер кадра")
    with LOCK:
        row=session(db,request,sid)
        if row["ended"] or row["camera"] != key:
            raise DomainError(409,"Замер остановлен или камера сменилась.")
        if seq==row["frame_no"]:
            return packet({"seq":seq,"boxes":[],"occupancy":row["occupancy"]})
        if seq!=row["frame_no"]+1:
            raise DomainError(409,"Порядок кадров нарушен. Подключи камеру заново.")
        boxes=field_detector.detect(decode_frame(request.field("jpeg")))
        clock=time.monotonic()
        cached=tracks.get(sid)
        gap=row["frame_no"]>0 and (not cached or clock-cached[1]>1.6)
        tracker=cached[0] if cached and not gap else Counter(row["axis"],row["line"],row["inside"],row["gate_start"],row["gate_end"])
        changes=tracker.update(boxes,clock)
        at,occupancy,uncertain=timestamp(),row["occupancy"],row["discrepancy"]
        try:
            with write(db):
                if gap:
                    event(db,sid,at,"gap",0,occupancy)
                    uncertain=1
                for change in changes:
                    if occupancy+change<0:
                        uncertain=1
                    previous=occupancy
                    occupancy=max(0,occupancy+change)
                    event(db,sid,at,"enter" if change==1 else "exit",occupancy-previous,occupancy)
                minute=at[:16]+":00+00:00"
                db.execute("""INSERT INTO field_minute VALUES (?,?,?,?,1) ON CONFLICT(session_id,minute)
                           DO UPDATE SET low=min(low,excluded.low),high=max(high,excluded.high),frames=frames+1""",
                           (sid,minute,occupancy,occupancy))
                db.execute("UPDATE field_session SET frame_no=?,occupancy=?,last_frame=?,discrepancy=? WHERE id=?",
                           (seq,occupancy,at,uncertain,sid))
        except Exception:
            tracks.pop(sid,None)
            raise
        tracks[sid]=(tracker,clock)
    return packet({"seq":seq,"boxes":boxes,"occupancy":occupancy})


def change(request,db,sid,action):
    allowed(request,True)
    key=camera_key(request) if action=="pause" else None
    value=whole(request.field("count"),0,10000,"Людей внутри") if action=="recount" else None
    def save():
        row=session(db,request,sid)
        if row["ended"]:
            raise DomainError(409,"Замер уже завершён.")
        if action=="pause" and row["camera"]!=key:
            raise DomainError(409,"Камера уже отключена.")
        count=row["occupancy"] if value is None else value
        at=timestamp()
        if action=="recount":
            db.execute("UPDATE field_session SET occupancy=?,discrepancy=0 WHERE id=?",(count,sid))
        else:
            db.execute("UPDATE field_session SET camera=NULL,discrepancy=?,ended=? WHERE id=?",
                       (1 if action=="pause" else row["discrepancy"],at if action=="end" else None,sid))
        event(db,sid,at,ACTION_KIND[action],count-row["occupancy"],count)
        return sid
    with LOCK:
        services.command(db,request.user,"field."+action,request.field("request_id"),[sid,key,value],save)
        if action!="recount":
            tracks.pop(sid,None)
    if action=="pause":
        return packet({"paused":True})
    return 303,b"",[("Location",PATH+"/"+sid)]


ACTION_KIND={"end":"end","pause":"pause","recount":"recount"}


def reference(request,db,sid):
    allowed(request,True)
    counts=[whole(request.field(key),0,99999,"Число проходов") for key in ("entered","exited")]
    def save():
        row=session(db,request,sid)
        if not row["ended"]:
            raise DomainError(409,"Сначала заверши замер.")
        db.execute("""INSERT INTO field_reference VALUES (?,?,?,?) ON CONFLICT(session_id)
                   DO UPDATE SET entered=excluded.entered, exited=excluded.exited, recorded=excluded.recorded""",
                   (sid,*counts,timestamp()))
        return sid
    services.command(db,request.user,"field.reference",request.field("request_id"),[sid,*counts],save)
    return 303,b"",[("Location",PATH+"/"+sid)]


def export(request,db,sid):
    allowed(request)
    row=session(db,request,sid)
    out=io.StringIO()
    writer=csv.writer(out)
    reference_row=db.execute("SELECT entered,exited FROM field_reference WHERE session_id=?",(sid,)).fetchone()
    witness=tuple(reference_row) if reference_row else ("","")
    writer.writerow(("session","mode","capacity","initial","time_utc","event","change","inside_after","observer_in","observer_out"))
    for rec in db.execute("SELECT at,kind,delta,occupancy FROM field_event WHERE session_id=? ORDER BY id",(sid,)):
        writer.writerow((sid,row["kind"],row["capacity"],row["initial_count"],*rec,*witness))
    return 200,("\ufeff"+out.getvalue()).encode(),[("Content-Type","text/csv; charset=utf-8"),
            ("Content-Disposition",f'attachment; filename="coworking-{sid[:8]}.csv"'),("Cache-Control","no-store")]
