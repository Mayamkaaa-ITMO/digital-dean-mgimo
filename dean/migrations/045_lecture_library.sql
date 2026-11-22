CREATE TABLE lecture_library_favorite (
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    course TEXT NOT NULL,
    PRIMARY KEY (account_id, course)
);
CREATE TABLE lecture_library_progress (
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    course TEXT NOT NULL,
    lesson TEXT NOT NULL,
    seconds INTEGER NOT NULL CHECK(seconds >= 0),
    completed INTEGER NOT NULL CHECK(completed IN (0,1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, course, lesson)
);
CREATE TABLE lecture_library_material (
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    course TEXT NOT NULL,
    lesson TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (account_id, course, lesson)
);
CREATE TABLE lecture_library_quiz (
    account_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    course TEXT NOT NULL,
    lesson TEXT NOT NULL,
    question INTEGER NOT NULL CHECK(question >= 0),
    choice INTEGER NOT NULL CHECK(choice >= 0),
    PRIMARY KEY (account_id, course, lesson, question)
);
