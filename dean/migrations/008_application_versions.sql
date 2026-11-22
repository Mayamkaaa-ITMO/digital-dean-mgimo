ALTER TABLE application ADD COLUMN form_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE application ADD COLUMN resolution TEXT NOT NULL DEFAULT '';
UPDATE application AS a SET form_json=(
    SELECT json_object(
      'version',1,'service',s.id,'name',s.name,'target_days',s.target_days,
      'document_kind',s.document_kind,'result','','required_file',json('false'),
      'fields',json(CASE
        WHEN s.document_kind='enrolment' THEN
          '[{"key":"purpose","kind":"text","label":"Цель получения","limit":200,"minimum":3}]'
        WHEN s.document_kind='transcript' THEN
          '[{"key":"purpose","kind":"text","label":"Цель получения","limit":200,"minimum":3},
            {"key":"period","kind":"select","label":"Период выписки","choices":{"current":"Текущий семестр","all":"Все доступные семестры"},"default":"current"}]'
        ELSE '[{"key":"details","kind":"textarea","label":"Описание","limit":1000,"minimum":5}]' END)
    ) FROM service s WHERE s.id=a.service_id AND s.university_id=a.university_id
);

CREATE TABLE application_attachment (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    name TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('image/png','text/plain')),
    content BLOB NOT NULL CHECK (length(content) BETWEEN 1 AND 2097152),
    added_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);
CREATE INDEX attachment_application ON application_attachment(application_id,active);

CREATE TABLE application_submission (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    version INTEGER NOT NULL,
    form_json TEXT NOT NULL,
    answers_json TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    UNIQUE(application_id,version)
);
CREATE TABLE application_submission_file (
    submission_id TEXT NOT NULL REFERENCES application_submission(id),
    attachment_id TEXT NOT NULL REFERENCES application_attachment(id),
    PRIMARY KEY(submission_id,attachment_id)
);
INSERT INTO application_submission
SELECT lower(hex(randomblob(16))),a.id,-h.id,a.form_json,h.answers_json,h.created_at
FROM application_history h JOIN application a ON a.id=h.application_id
WHERE h.status IN ('submitted','resubmitted') AND h.event_kind='status' AND h.answers_json IS NOT NULL
ORDER BY h.id;

ALTER TABLE notification RENAME TO notification_v7;
CREATE TABLE notification (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    UNIQUE (university_id,student_id,event_key),
    FOREIGN KEY (university_id,student_id) REFERENCES account(university_id,id)
);
INSERT INTO notification SELECT * FROM notification_v7;
DROP TABLE notification_v7;
CREATE INDEX notification_owner ON notification(university_id,student_id,created_at);
