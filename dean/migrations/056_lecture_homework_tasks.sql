CREATE TABLE lecture_homework_task (
    lecture_id TEXT NOT NULL REFERENCES lab_lecture(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL REFERENCES account(id),
    task_id TEXT NOT NULL UNIQUE REFERENCES task(id) ON DELETE CASCADE,
    PRIMARY KEY (lecture_id, owner_id)
);

CREATE INDEX coursework_student_task ON coursework_student(task_id, student_id);

WITH candidates AS (
    SELECT l.id AS lecture_id, t.owner_id, t.id AS task_id, t.rowid AS task_order,
           count(*) OVER (PARTITION BY t.id) AS lecture_count
    FROM lab_lecture l
    JOIN task t ON t.university_id = l.university_id
        AND t.title = 'Домашняя работа · ' || trim(substr(l.title, 1, 100))
        AND t.description = trim(substr(l.homework, 1, 1600), char(9, 10, 13, 32))
        AND t.created_at >= l.created
    JOIN account a ON a.id = t.owner_id
    WHERE a.role = 'student' AND l.homework <> ''
        AND (l.owner = a.id OR (l.visibility = 'group' AND l.group_id = a.group_id AND l.period_id = a.period_id))
        AND t.subject_id IS NULL AND t.due_at IS NULL AND t.plan_id IS NULL
)
INSERT OR IGNORE INTO lecture_homework_task (lecture_id, owner_id, task_id)
SELECT lecture_id, owner_id, task_id FROM candidates WHERE lecture_count = 1 ORDER BY task_order;

UPDATE task
SET state = 'done', version = version + 1, updated_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
WHERE state <> 'done' AND id IN (
    SELECT w.task_id FROM coursework_student w
    JOIN coursework_assignment a ON a.id = w.assignment_id
    WHERE w.status IN ('submitted', 'review', 'graded') OR a.state = 'archived'
);
