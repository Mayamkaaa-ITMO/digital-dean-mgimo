CREATE TABLE team (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    moderator_id TEXT,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'project' CHECK(kind IN ('project','club')),
    created_at TEXT NOT NULL,
    UNIQUE(university_id,id),
    FOREIGN KEY(university_id,owner_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,moderator_id) REFERENCES account(university_id,id)
);
CREATE TABLE team_member (
    team_id TEXT NOT NULL REFERENCES team(id),
    student_id TEXT NOT NULL REFERENCES account(id),
    joined_at TEXT NOT NULL,
    removed_at TEXT,
    allow_availability INTEGER NOT NULL DEFAULT 0 CHECK(allow_availability IN (0,1)),
    PRIMARY KEY(team_id,student_id)
);
CREATE INDEX team_member_student ON team_member(student_id,removed_at);
CREATE TABLE team_invitation (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES team(id),
    student_id TEXT NOT NULL REFERENCES account(id),
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined','revoked','expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE UNIQUE INDEX team_open_invitation ON team_invitation(team_id,student_id) WHERE status='pending';
CREATE INDEX team_invitee ON team_invitation(student_id);

CREATE TABLE team_task (
    task_id TEXT PRIMARY KEY REFERENCES task(id),
    team_id TEXT NOT NULL REFERENCES team(id),
    assignee_id TEXT REFERENCES account(id)
);
CREATE INDEX team_tasks_by_team ON team_task(team_id);
CREATE TABLE team_note (
    team_id TEXT NOT NULL REFERENCES team(id),
    note_id TEXT NOT NULL REFERENCES note(id) ON DELETE CASCADE,
    granted_at TEXT NOT NULL,
    PRIMARY KEY(team_id,note_id)
);
CREATE TABLE team_message (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES team(id),
    task_id TEXT REFERENCES team_task(task_id),
    author_id TEXT NOT NULL REFERENCES account(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    hidden_at TEXT
);
CREATE INDEX team_message_posts ON team_message(team_id,task_id,created_at);
CREATE INDEX team_message_rate ON team_message(author_id,created_at);
CREATE TABLE team_report (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES team_message(id),
    reporter_id TEXT NOT NULL REFERENCES account(id),
    reason TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT REFERENCES account(id),
    outcome TEXT CHECK(outcome IN ('hidden','dismissed')),
    decision TEXT,
    UNIQUE(message_id,reporter_id)
);

CREATE TABLE team_meeting (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES team(id),
    organizer_id TEXT NOT NULL REFERENCES account(id),
    title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    CHECK(starts_at<ends_at)
);
CREATE TABLE team_meeting_response (
    meeting_id TEXT NOT NULL REFERENCES team_meeting(id),
    student_id TEXT NOT NULL REFERENCES account(id),
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined')),
    PRIMARY KEY(meeting_id,student_id)
);
CREATE INDEX team_meeting_owner ON team_meeting_response(student_id,status);
CREATE TABLE team_meeting_history (
    id INTEGER PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES team_meeting(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    action TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE club (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    FOREIGN KEY(university_id,id) REFERENCES team(university_id,id)
);
