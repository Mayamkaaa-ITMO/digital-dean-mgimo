"""Versioned work in the existing course, task and grading services."""
import hashlib
import json

from . import applications, attachment, learning, services, workspace
from .db import write
from .domain import DomainError, field, identifier, require, role, timestamp
from .schedule import edit_time

MODES={"text":"Текст","file":"Файл","either":"Текст или файл","both":"Текст и файл"}
STATES={"draft":"Черновик","published":"Опубликована","archived":"Снята с публикации"}
WORK_STATES={"draft":"Можно ответить","submitted":"Сдано","review":"Проверяется",
             "returned":"На доработке","graded":"Результат опубликован"}


def eligible(db,user,staff_id):
    role(user,"staff")
    return list(db.execute("""
        SELECT DISTINCT u.group_id,u.period_id,r.subject_id,g.name AS group_name,s.name AS subject
        FROM staff_group sg JOIN account u ON u.university_id=sg.university_id AND u.group_id=sg.group_id
        JOIN staff_group owner ON owner.university_id=u.university_id AND owner.group_id=u.group_id AND owner.staff_id=?
        JOIN study_group g ON g.university_id=u.university_id AND g.id=u.group_id
        JOIN learning_result r ON r.university_id=u.university_id AND r.student_id=u.id AND r.period_id=u.period_id
        JOIN subject s ON s.id=r.subject_id AND s.university_id=r.university_id
        WHERE sg.staff_id=? AND sg.university_id=? AND u.role='student'
        ORDER BY g.name,s.name
    """,(user["id"],staff_id,user["university_id"])))


def grant_in_transaction(db,actor,staff_id,scope):
    if scope not in {(row["group_id"],row["period_id"],row["subject_id"]) for row in eligible(db,actor,staff_id)}:
        raise DomainError(403,"Группа или дисциплина вне доступной области преподавателя.")
    db.execute("INSERT OR IGNORE INTO instructor_scope VALUES (?,?,?,?,?)",
               (staff_id,actor["university_id"],*scope))


def grant_demo(db,username,group,subject):
    with write(db):
        actor=require(db.execute("SELECT * FROM account WHERE username=? AND role='staff'",(username,)).fetchone())
        scope=next(((row["group_id"],row["period_id"],row["subject_id"]) for row in eligible(db,actor,actor["id"])
                    if row["group_id"]==group and row["subject_id"]==subject),None)
        if not scope:
            raise DomainError(422,"Группа или предмет не найдены в области сотрудника.")
        grant_in_transaction(db,actor,actor["id"],scope)


def teacher_scopes(db,user):
    role(user,"staff")
    return list(db.execute("""
        SELECT i.*,g.name AS group_name,s.name AS subject FROM instructor_scope i
        JOIN staff_group sg ON sg.university_id=i.university_id AND sg.staff_id=i.instructor_id AND sg.group_id=i.group_id
        JOIN study_group g ON g.id=i.group_id AND g.university_id=i.university_id
        JOIN subject s ON s.id=i.subject_id AND s.university_id=i.university_id
        WHERE i.instructor_id=? AND i.university_id=? ORDER BY g.name,s.name
    """,(user["id"],user["university_id"])))


def assignment(db,user,aid=None):
    scopes={(row["group_id"],row["period_id"],row["subject_id"]) for row in teacher_scopes(db,user)}
    rows=db.execute("""
        SELECT a.*,d.title,d.description,d.due_at,d.response_mode,d.late_allowed,d.attempt_limit,d.max_points,d.rubric_json
        FROM coursework_assignment a JOIN coursework_definition d
          ON d.assignment_id=a.id AND d.version=a.definition_version
        WHERE a.university_id=? AND a.instructor_id=? ORDER BY a.created_at DESC,a.id
    """,(user["university_id"],user["id"]))
    filtered=[row for row in rows if (row["group_id"],row["period_id"],row["subject_id"]) in scopes]
    return require(next((row for row in filtered if row["id"]==aid),None)) if aid else filtered


def definition_values(user,fields):
    title=field(fields.get("title",""),"Название",120)
    description=field(fields.get("description",""),"Условия работы",2000)
    due=timestamp(edit_time(fields.get("due",""),user["timezone"]))
    mode=fields.get("response_mode")
    count=learning.optional_int(fields.get("attempt_limit"),"Попытки",8)
    if mode not in MODES or not count:
        raise DomainError(422,"Выбери формат ответа и от одной до восьми попыток.")
    rubric=[]
    for line in fields.get("rubric","").strip().splitlines():
        name, sep, amount=line.rpartition("|")
        maximum=learning.optional_int(amount.strip(),"Максимум критерия",1000) if sep else None
        if not maximum:
            raise DomainError(422,"Каждый критерий введи как «Название | максимум».")
        rubric.append((field(name,"Критерий",100),maximum))
    total=sum(amount for _,amount in rubric)
    if not 1<=len(rubric)<=6 or total>1000 or len({name.casefold() for name,_ in rubric})!=len(rubric):
        raise DomainError(422,"Нужны 1–6 различных критериев с общей шкалой до 1000.")
    return (title,description,due,mode,int(fields.get("late")=="1"),count,total,json.dumps(rubric,ensure_ascii=False))


def save_assignment(db,user,fields,key,aid=None):
    role(user,"staff")
    values=definition_values(user,fields)

    def apply():
        if aid:
            row=assignment(db,user,aid)
            applications.version_check(row,fields.get("version"))
            if row["state"]=="archived":
                raise DomainError(409,"Для снятой работы сохранилась история. Для новых условий создай работу.")
            chosen=aid
            revision=row["definition_version"]+1
            db.execute("UPDATE coursework_assignment SET definition_version=?,version=version+1 WHERE id=?",
                       (revision,chosen))
        else:
            scope=tuple(fields.get("scope","").split("|"))
            if scope not in {(row["group_id"],row["period_id"],row["subject_id"]) for row in teacher_scopes(db,user)}:
                raise DomainError(403,"Преподавателю не назначена эта дисциплина.")
            chosen,revision=identifier(),1
            db.execute("""
                INSERT INTO coursework_assignment(id,instructor_id,university_id,group_id,period_id,subject_id,state,created_at)
                VALUES (?,?,?,?,?,?,'draft',?)
            """,(chosen,user["id"],user["university_id"],*scope,timestamp()))
        db.execute("INSERT INTO coursework_definition VALUES (?,?,?,?,?,?,?,?,?,?,?)",(chosen,revision,*values,timestamp()))
        return chosen
    return services.command(db,user,"coursework.save",key,[aid,fields.get("version"),fields.get("scope"),values],apply)


def change_publication(db,user,aid,action,expected,key):
    def apply():
        row=assignment(db,user,aid)
        applications.version_check(row,expected)
        if action=="archive":
            if row["state"]=="archived":
                return aid
            db.execute("UPDATE coursework_assignment SET state='archived',version=version+1 WHERE id=?",(aid,))
            db.execute("""
                UPDATE task SET state='done',updated_at=?,version=version+1 WHERE state<>'done' AND id IN
                (SELECT task_id FROM coursework_student WHERE assignment_id=?)
            """,(timestamp(),aid))
            return aid
        if row["state"]=="archived":
            raise DomainError(409,"Снятая работа сохранена в истории.")
        if row["state"]=="published":
            return aid
        if row["due_at"]<=timestamp() and not row["late_allowed"]:
            raise DomainError(422,"Опубликуй будущий срок или явно разреши позднюю сдачу.")
        db.execute("UPDATE coursework_assignment SET state='published',version=version+1 WHERE id=?",(aid,))
        students=db.execute("""
            SELECT a.*,r.id AS result_id FROM account a JOIN learning_result r
              ON r.university_id=a.university_id AND r.student_id=a.id
            WHERE a.university_id=? AND a.group_id=? AND a.period_id=? AND a.role='student'
               AND r.period_id=? AND r.subject_id=?
        """,(row["university_id"],row["group_id"],row["period_id"],row["period_id"],row["subject_id"])).fetchall()
        if not students:
            raise DomainError(409,"В выбранной группе нет студентов этой дисциплины.")
        for student in students:
            wid,component=identifier(),identifier()
            task=workspace.add_task(db,student,("Сдать · "+row["title"][:111],"Демонстрационная учебная работа",
                                              row["subject_id"],row["due_at"],"normal"))
            db.execute("""
                INSERT INTO assessment(id,result_id,code,title,kind,max_points,weight_bp,points,state,due_at)
                VALUES (?,?,?,?,'coursework',?,0,NULL,'scheduled',?)
            """,(component,student["result_id"],"work-"+aid,row["title"],row["max_points"],row["due_at"]))
            db.execute("INSERT INTO coursework_student(id,assignment_id,student_id,task_id,assessment_id) VALUES (?,?,?,?,?)",
                       (wid,aid,student["id"],task,component))
            services.notify(db,row["university_id"],student["id"],"work",wid,"work:published:"+wid,
                            "Опубликована учебная работа",row["title"],timestamp())
        return aid
    return services.command(db,user,"coursework."+action,key,[aid,expected],apply)


DETAIL = """
    SELECT w.*, a.state AS assignment_state,a.instructor_id,a.university_id,a.group_id,a.period_id,a.subject_id,
        d.version AS rules_version,d.title,d.description,d.due_at,d.response_mode,d.late_allowed,d.attempt_limit,
        d.max_points,d.rubric_json,acc.full_name AS student_name,s.name AS subject,c.result_id
    FROM coursework_student w JOIN coursework_assignment a ON a.id=w.assignment_id
    JOIN coursework_definition d ON d.assignment_id=a.id AND d.version=coalesce(w.definition_version,a.definition_version)
    JOIN account acc ON acc.id=w.student_id
    JOIN subject s ON s.id=a.subject_id AND s.university_id=a.university_id
    LEFT JOIN assessment c ON c.id=w.assessment_id
"""


def student_list(db,user,wid=None,*,task_id=None,subject_id=None,period_id=None):
    role(user,"student")
    where=" WHERE w.student_id=? AND a.university_id=?"
    data=(user["id"],user["university_id"])
    for column, value in (("w.task_id", task_id), ("a.subject_id", subject_id), ("a.period_id", period_id),
                          ("a.group_id", user["group_id"] if subject_id else None)):
        if value is not None:
            where += f" AND {column}=?"
            data += (value,)
    if wid:
        return require(db.execute(DETAIL+where+" AND w.id=?",(*data,wid)).fetchone())
    return list(db.execute(DETAIL+where+" ORDER BY d.due_at,w.id",data))


def student_status(row):
    if row["assignment_state"] == "archived":
        return STATES["archived"]
    if row["status"] in {"draft", "returned"} and not accepts_submission_at(row, timestamp()):
        return "Срок сдачи прошёл"
    return WORK_STATES[row["status"]]


def student_next_step(row):
    if row["assignment_state"] == "archived":
        return "Преподаватель снял работу с публикации. Ответы и результаты остаются в истории."
    if row["status"] in {"draft", "returned"}:
        if not accepts_submission_at(row, timestamp()):
            return "Срок сдачи прошёл. Преподаватель не принимает ответы после срока."
        return "Открой условия и отправь ответ в карточке учебной работы."
    if row["status"] == "graded":
        return "Баллы и замечания доступны в карточке работы."
    return "Ответ у преподавателя. О результате или доработке придёт уведомление."


def accepts_submission_at(row, stamp):
    return row["late_allowed"] or stamp <= row["due_at"]


def teacher_work(db,user,wid):
    row=require(db.execute(DETAIL+" WHERE w.id=? AND a.university_id=? AND a.instructor_id=?",
                           (wid,user["university_id"],user["id"])).fetchone())
    assignment(db,user,row["assignment_id"])
    return row


def submissions(db,user,aid):
    assignment(db,user,aid)
    return list(db.execute(DETAIL+" WHERE w.assignment_id=? ORDER BY w.status IN ('submitted','review') DESC,acc.full_name,w.id",(aid,)))


def attempts(db,wid,staff=False):
    visibility="" if staff else " AND r.decision<>'draft'"
    return list(db.execute("""
        SELECT a.*,r.points_json,r.feedback,r.decision,f.id AS file_id,f.name AS filename
        FROM coursework_attempt a LEFT JOIN coursework_review r ON r.attempt_id=a.id"""+visibility+"""
        LEFT JOIN coursework_file f ON f.attempt_id=a.id AND f.work_id=a.work_id
        WHERE a.work_id=? ORDER BY a.number DESC
    """,(wid,)))


def draft_file(db,wid):
    return db.execute("SELECT id,name FROM coursework_file WHERE work_id=? AND attempt_id IS NULL",(wid,)).fetchone()


def answer_version(row):
    return f'{row["version"]}:{row["rules_version"]}'


def allowed_draft(db,user,wid,expected):
    row=student_list(db,user,wid)
    if answer_version(row)!=expected:
        raise DomainError(409,"Ответ или условия изменились. Сверь версию и отправь заново.")
    if row["status"] not in {"draft","returned"} or row["assignment_state"]!="published":
        raise DomainError(409,"Новый ответ для этой работы недоступен.")
    return row


def pin_definition(db,row):
    db.execute("UPDATE coursework_student SET definition_version=?,version=version+1 WHERE id=?",
               (row["rules_version"],row["id"]))


def update_submission_task(db, work, state, stamp):
    db.execute("""
        UPDATE task SET state=?,updated_at=?,version=version+1
        WHERE id=? AND owner_id=? AND university_id=? AND state<>?
    """,(state,stamp,work["task_id"],work["student_id"],work["university_id"],state))


def save_answer(db,user,wid,text,expected,action,key):
    text=field(text,"Ответ",2400,0)

    def apply():
        row=allowed_draft(db,user,wid,expected)
        pin_definition(db,row)
        if action=="draft":
            db.execute("UPDATE coursework_student SET draft_text=? WHERE id=?",(text,wid))
            return wid
        upload=draft_file(db,wid)
        has_text,has_file=bool(text),upload is not None
        mode=row["response_mode"]
        valid=(has_text and not has_file) if mode=="text" else (has_file and not has_text) if mode=="file" else (
            has_text and has_file if mode=="both" else has_text or has_file)
        if not valid:
            raise DomainError(422,"Подготовь ответ в объявленном формате.")
        stamp=timestamp()
        late=stamp>row["due_at"]
        if not accepts_submission_at(row, stamp):
            raise DomainError(409,"Срок истёк. Поздняя сдача отключена для этой версии.")
        number=db.execute("SELECT count(*) FROM coursework_attempt WHERE work_id=?",(wid,)).fetchone()[0]+1
        if number>row["attempt_limit"]:
            raise DomainError(409,"Допустимые попытки закончились.")
        attempt=identifier()
        db.execute("INSERT INTO coursework_attempt VALUES (?,?,?,?,?,?,?)",
                   (attempt,wid,number,row["rules_version"],text,stamp,int(late)))
        db.execute("UPDATE coursework_file SET attempt_id=? WHERE work_id=? AND attempt_id IS NULL",(attempt,wid))
        db.execute("UPDATE coursework_student SET status='submitted',draft_text='' WHERE id=?",(wid,))
        update_submission_task(db,row,"done",stamp)
        db.execute("UPDATE assessment SET state='awaiting',max_points=?,title=?,due_at=? WHERE id=?",
                   (row["max_points"],row["title"],row["due_at"],row["assessment_id"]))
        return wid
    return services.command(db,user,"coursework.answer."+action,key,[wid,text,expected],apply)


def attach(db,user,wid,upload,expected,key):
    name,media,data=attachment.validated(*upload) if upload else ("","",b"")

    def apply():
        row=allowed_draft(db,user,wid,expected)
        if upload and row["response_mode"]=="text":
            raise DomainError(422,"Эта работа принимает только текст.")
        pin_definition(db,row)
        db.execute("DELETE FROM coursework_file WHERE work_id=? AND attempt_id IS NULL",(wid,))
        if upload:
            db.execute("INSERT INTO coursework_file VALUES (?,?,NULL,?,?,?)",(identifier(),wid,name,media,data))
        return wid
    return services.command(db,user,"coursework.attach",key,[wid,expected,name,hashlib.sha256(data).hexdigest()],apply)


def file(db,user,fid):
    row=require(db.execute("SELECT * FROM coursework_file WHERE id=?",(fid,)).fetchone())
    if user["role"]=="student":
        student_list(db,user,row["work_id"])
    else:
        teacher_work(db,user,row["work_id"])
        if row["attempt_id"] is None:
            raise DomainError(404,"Файл недоступен.")
    return row


def score_assessment(db,user,work,points,stamp):
    old=require(db.execute("SELECT * FROM assessment WHERE id=?",(work["assessment_id"],)).fetchone())
    result=require(db.execute("SELECT * FROM learning_result WHERE id=?",(old["result_id"],)).fetchone())
    version=result["version"]+1
    db.execute("UPDATE assessment SET points=?,state='graded',max_points=? WHERE id=?",
               (sum(points),work["max_points"],old["id"]))
    db.execute("UPDATE learning_result SET version=? WHERE id=?",(version,result["id"]))
    before=json.dumps({"record":old["title"],"points":old["points"],"state":old["state"]},ensure_ascii=False)
    after=json.dumps({"record":old["title"],"points":sum(points),"state":"graded"},ensure_ascii=False)
    db.execute("""
        INSERT INTO grade_revision(result_id,actor_id,version,old_json,new_json,created_at) VALUES (?,?,?,?,?,?)
    """,(result["id"],user["id"],version,before,after,stamp))


def review(db,user,wid,action,inputs,key):
    role(user,"staff")
    feedback=field(inputs.get("feedback",""),"Замечания",1800,1 if action=="return" else 0)

    def apply():
        row=teacher_work(db,user,wid)
        applications.version_check(row,inputs.get("version"))
        if row["status"] not in {"submitted","review"}:
            raise DomainError(409,"Этот ответ уже обработан.")
        latest=attempts(db,wid,staff=True)[0]
        if inputs.get("attempt")!=latest["id"]:
            raise DomainError(409,"Открой текущую попытку.")
        values=[learning.optional_int(inputs.get("points_"+str(index)),name,maximum)
                for index,(name,maximum) in enumerate(json.loads(row["rubric_json"]))]
        if action=="publish" and any(value is None for value in values):
            raise DomainError(422,"Укажи баллы по каждому критерию, включая ноль.")
        if action=="return" and latest["number"]>=row["attempt_limit"]:
            raise DomainError(409,"Лимит попыток исчерпан для этой версии.")
        status,decision={"draft":("review","draft"),"return":("returned","returned"),"publish":("graded","graded")}[action]
        stamp=timestamp()
        db.execute("""
            INSERT INTO coursework_review VALUES (?,?,?,?,?,?)
            ON CONFLICT(attempt_id) DO UPDATE SET points_json=excluded.points_json, feedback=excluded.feedback,
                decision=excluded.decision, updated_at=excluded.updated_at
        """,(latest["id"],user["id"],json.dumps(values),feedback,decision,stamp))
        db.execute("UPDATE coursework_student SET status=?,version=version+1 WHERE id=?",(status,wid))
        if action=="return" and row["assignment_state"]=="published":
            update_submission_task(db,row,"todo",stamp)
        else:
            update_submission_task(db,row,"done",stamp)
        if status=="graded":
            score_assessment(db,user,row,values,stamp)
        if decision!="draft":
            label="Результат опубликован" if decision=="graded" else "Работа возвращена на доработку"
            kind="work_result" if decision=="graded" else "work"
            services.notify(db,row["university_id"],row["student_id"],kind,wid,
                            f"work:{decision}:{latest['id']}",label,"Открой учебную работу в кабинете.",stamp)
        return wid
    return services.command(db,user,"coursework.review."+action,key,[wid,feedback,inputs],apply)
