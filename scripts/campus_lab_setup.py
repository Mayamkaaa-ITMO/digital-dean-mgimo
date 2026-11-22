"""Install the bundled demo campus content for the built-in accounts."""
from contextlib import closing
import hashlib
import json

from dean import coursework, lecture_channel, room_occupancy
from dean.db import connect, database_path, migrate, write
from dean.domain import timestamp
from dean.seed import UNIVERSITY, ZONE, seed_id
from scripts import demo_content


def example_recording(db, teacher):
    sample_id = seed_id("campus-lab:lecture:rhetoric")
    details = json.loads(lecture_channel.SAMPLE.with_suffix(".json").read_text())
    with write(db):
        if db.execute("SELECT 1 FROM lab_lecture WHERE id=?", (sample_id,)).fetchone():
            db.execute("""UPDATE lab_lecture SET subject_id='' WHERE id=? AND owner=?
                        AND subject_id='databases' AND title=? AND transcript=?""",
                       (sample_id, teacher["id"], details["title"], details["transcript"]))
            return
        scope = next((row for row in coursework.teacher_scopes(db, teacher)
                      if row["group_id"] == "demo-ai" and row["subject_id"] == "databases"), None)
        if not scope:
            return
        audio = lecture_channel.SAMPLE.read_bytes()
        if hashlib.sha256(audio).hexdigest() != details["sha256"]:
            raise RuntimeError("Демонстрационная запись не соответствует проверенной расшифровке.")
        stamp = timestamp()
        db.execute("""INSERT INTO lab_lecture
            (id,owner,university_id,group_id,period_id,subject_id,title,created,ended,transcript,notes,homework,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (sample_id, teacher["id"], UNIVERSITY, scope["group_id"], scope["period_id"], "",
                    details["title"], stamp, stamp, details["transcript"], details["notes"], details["homework"],
                    details["source"]))
        db.execute("INSERT INTO lab_segment VALUES (?,?,?,?)", (sample_id, 0, "audio/mpeg", audio))


def setup(db):
    def account(login, role):
        row = db.execute("SELECT * FROM account WHERE id=? AND username=? AND university_id=? AND role=?",
                         (seed_id(login), login, UNIVERSITY, role)).fetchone()
        return dict(row, timezone=ZONE.key) if row else None

    teacher = account("staff", "staff")
    if not teacher:
        return
    students = {name: student for name in ("student-a", "student-b") if (student := account(name, "student"))}
    has_demo_class = any(row["group_id"] == "demo-ai" and row["subject_id"] == "databases"
                         for row in coursework.eligible(db, teacher, teacher["id"]))
    if has_demo_class:
        coursework.grant_demo(db, "staff", "demo-ai", "databases")
        example_recording(db, teacher)
    if room_occupancy.available(teacher):
        room_occupancy.populate(db, teacher, seed_id("campus-lab:traffic-history"))
    demo_content.populate(db, teacher, students)


def main():
    with closing(connect(database_path(), create=False)) as db:
        migrate(db)
        setup(db)


if __name__ == "__main__":
    main()
