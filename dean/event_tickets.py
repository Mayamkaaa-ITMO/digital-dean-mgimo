from os import getenv
"""Single-registration demo tickets and organizer-authorized attendance."""
from datetime import timedelta
import struct
from uuid import UUID
import zlib

from . import attachment, auth, reservations, services
from .clock import now
from .db import write
from .domain import DomainError, field, identifier, require, role, timestamp
from .vendor.qrcodegen import QrCode

PREFIX="DEAN1-"
DEMO="spbstu-demo"


def png(code):
    qr=QrCode.encode_text(code,QrCode.Ecc.MEDIUM)
    scale,border=5,4
    size=(qr.get_size()+2*border)*scale
    header=struct.pack(">IIBBBBB",size,size,8,0,0,0,0)
    data=b"".join(
        (b"\0"+bytes(0 if qr.get_module(x-border,y-border) else 255
                     for x in range(qr.get_size()+2*border) for _ in range(scale)))*scale
        for y in range(qr.get_size()+2*border))
    return attachment.PNG+attachment.png_chunk(b"IHDR",header)+attachment.png_chunk(
        b"IDAT",zlib.compress(data))+attachment.png_chunk(b"IEND",b"")


def audit(db,user,reservation,ticket,action,reason=""):
    db.execute("""
        INSERT INTO event_ticket_history(reservation_id,ticket_id,actor_id,action,reason,created_at)
        VALUES (?,?,?,?,?,?)
    """,(reservation,ticket,user["id"],action,reason,timestamp()))


def current(db,rid):
    return db.execute("SELECT id,issued_at,used_at FROM event_ticket WHERE reservation_id=? AND revoked_at IS NULL",(rid,)).fetchone()


def issue(db,user,rid,expected,key):
    role(user,"student")
    try:
        request_id=UUID(key).hex
    except (TypeError,ValueError):
        raise DomainError(422,"Открой билет заново.") from None
    secret=auth.token()
    with write(db):
        booking=reservations.get_booking(db,user,rid)
        if booking["kind"]!="activity" or (booking["university_id"]!=DEMO and getenv("DEAN_PRODUCT")!="1"):
            raise DomainError(404,"Билет не предусмотрен для этой записи.")
        if booking["status"]!="reserved" or booking["ends_at"]<=timestamp():
            raise DomainError(409,"Выдача билета доступна для действующей регистрации.")
        old=current(db,rid)
        if expected!=(old["id"] if old else ""):
            raise DomainError(409,"Билет изменился. Открой регистрацию заново.")
        if db.execute("SELECT 1 FROM reservation_attendance WHERE reservation_id=?",(rid,)).fetchone():
            raise DomainError(409,"Посещение уже подтверждено.")
        if db.execute("SELECT 1 FROM event_ticket WHERE reservation_id=? AND request_id=?",(rid,request_id)).fetchone():
            raise DomainError(409,"Эта форма уже выдала билет.")
        issued=timestamp()
        if old:
            db.execute("UPDATE event_ticket SET revoked_at=? WHERE id=?",(issued,old["id"]))
            audit(db,user,rid,old["id"],"revoked","Перевыпуск")
        tid=identifier()
        db.execute("INSERT INTO event_ticket VALUES (?,?,?,?,?,NULL,NULL)",(tid,rid,auth.digest(secret),request_id,issued))
        audit(db,user,rid,tid,"issued")
    return PREFIX+secret


def organizer(db,user,sid):
    row=reservations.slot(db,user,sid,manage=True)
    if row["kind"]!="activity" or (row["university_id"]!=DEMO and getenv("DEAN_PRODUCT")!="1"):
        raise DomainError(404,"Проверка билетов доступна для своего мероприятия.")
    return row


def ticket_record(db,key,sid,*,by_code=False):
    column="t.token_hash" if by_code else "t.id"
    return db.execute("""
        SELECT t.*,r.status,r.student_id,r.university_id,a.full_name FROM event_ticket t
        JOIN reservation r ON r.id=t.reservation_id JOIN account a ON a.id=r.student_id
        WHERE """+column+"=? AND r.slot_id=?",(key,sid)).fetchone()


def valid(db,row,window):
    if not row or row["revoked_at"] or row["status"]!="reserved":
        return False
    if db.execute("SELECT state FROM managed_offer WHERE bookable_id=? AND state<>'published'",
                  (window["bookable_id"],)).fetchone():
        return False
    moment=now()
    return timestamp(moment+timedelta(minutes=30))>=window["starts_at"] and timestamp(
        moment-timedelta(minutes=15))<window["ends_at"]


def prepare(db,user,sid,code):
    organizer(db,user,sid)
    code=code.strip().removeprefix(PREFIX)
    with write(db):
        window=organizer(db,user,sid)
        stamp=now()
        db.execute("DELETE FROM event_ticket_check WHERE checked_at<?",(timestamp(stamp-timedelta(days=1)),))
        count=db.execute("""
            SELECT count(*) FROM event_ticket_check WHERE actor_id=? AND slot_id=? AND checked_at>?
        """,(user["id"],sid,timestamp(stamp-timedelta(minutes=1)))).fetchone()[0]
        if count>=12:
            raise DomainError(429,"Слишком много проверок за минуту.")
        db.execute("INSERT INTO event_ticket_check VALUES (?,?,?,?)",(identifier(),user["id"],sid,timestamp(stamp)))
        item=ticket_record(db,auth.digest(code),sid,by_code=True) if auth.TOKEN.fullmatch(code) else None
        review=None
        if valid(db,item,window):
            review=identifier()
            db.execute("INSERT INTO event_ticket_review VALUES (?,?,?,?,?,NULL)",
                       (review,item["id"],user["id"],sid,timestamp(stamp+timedelta(minutes=5))))
    if not review:
        raise DomainError(404,"Билет недоступен для этого события или времени.")
    return review


def reviewed(db,user,sid,review):
    window=organizer(db,user,sid)
    staged=require(db.execute("""
        SELECT * FROM event_ticket_review WHERE id=? AND actor_id=? AND slot_id=? AND expires_at>?
    """,(review,user["id"],sid,timestamp())).fetchone())
    row=ticket_record(db,staged["ticket_id"],sid)
    if not valid(db,row,window):
        raise DomainError(409,"Состояние билета изменилось. Проверь актуальный билет.")
    return row,staged,window


def redeem(db,user,sid,review,key):
    def apply():
        row,staged,window=reviewed(db,user,sid,review)
        rid=row["reservation_id"]
        if staged["applied_at"]:
            return rid
        booking=require(db.execute("SELECT * FROM reservation WHERE id=? AND status='reserved'",(rid,)).fetchone())
        if reservations.record_attendance(db,user,window,booking):
            audit(db,user,rid,row["id"],"confirmed")
        stamp=timestamp()
        db.execute("UPDATE event_ticket SET used_at=coalesce(used_at,?) WHERE id=?",(stamp,row["id"]))
        db.execute("UPDATE event_ticket_review SET applied_at=? WHERE id=?",(stamp,review))
        return rid

    return services.command(db,user,"event.ticket.confirm",key,[sid,review],apply)


def correct(db,user,sid,rid,reason,expected,key):
    reason=field(reason,"Причина исправления",400)

    def apply():
        window=organizer(db,user,sid)
        booking=require(db.execute("SELECT * FROM reservation WHERE id=? AND slot_id=?",(rid,sid)).fetchone())
        record=db.execute("SELECT confirmed_at FROM reservation_attendance WHERE reservation_id=?",(rid,)).fetchone()
        if not record or record["confirmed_at"]!=expected:
            raise DomainError(409,"Отметка уже изменилась. Обнови список.")
        award=db.execute("SELECT entry_id FROM portfolio_verification WHERE reservation_id=?",(rid,)).fetchone()
        if award:
            db.execute("DELETE FROM portfolio_verification WHERE reservation_id=?",(rid,))
            db.execute("UPDATE portfolio_entry SET content=?,version=version+1 WHERE id=?",
                       ("Организатор отозвал отметку участия.",award["entry_id"]))
        db.execute("DELETE FROM reservation_attendance WHERE reservation_id=?",(rid,))
        ticket=current(db,rid)
        stamp=timestamp()
        if ticket:
            db.execute("UPDATE event_ticket SET revoked_at=? WHERE id=?",(stamp,ticket["id"]))
        audit(db,user,rid,ticket["id"] if ticket else None,"corrected",reason)
        services.notify(db,window["university_id"],booking["student_id"],"booking",rid,f"booking:{rid}:corrected:{key}",
                        "Отметка участия отозвана","Состояние доступно в демонстрационном кабинете.",stamp)
        return rid
    return services.command(db,user,"event.attendance.correct",key,[sid,rid,reason,expected],apply)
