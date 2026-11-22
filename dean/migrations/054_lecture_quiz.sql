ALTER TABLE lab_lecture ADD COLUMN quiz_json TEXT;

CREATE TABLE lab_lecture_quiz_answer (
    lecture_id TEXT NOT NULL REFERENCES lab_lecture(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    question INTEGER NOT NULL CHECK(question >= 0),
    choice INTEGER NOT NULL CHECK(choice >= 0),
    PRIMARY KEY (lecture_id, account_id, question)
);
