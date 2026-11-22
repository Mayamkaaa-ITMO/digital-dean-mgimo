CREATE TABLE field_session (
 id TEXT PRIMARY KEY, university_id TEXT NOT NULL, room TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('field','practice')),
 capacity INTEGER NOT NULL CHECK(capacity>0), initial_count INTEGER NOT NULL CHECK(initial_count>=0),
 occupancy INTEGER NOT NULL CHECK(occupancy>=0), axis TEXT NOT NULL CHECK(axis IN ('x','y')),
 line INTEGER NOT NULL CHECK(line BETWEEN 20 AND 80), inside INTEGER NOT NULL CHECK(inside IN (-1,1)),
 started TEXT NOT NULL, ended TEXT, camera TEXT, frame_no INTEGER NOT NULL DEFAULT 0,
 last_frame TEXT, campus_lat REAL, campus_lon REAL, radius INTEGER NOT NULL DEFAULT 150, discrepancy INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE field_event (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES field_session(id), at TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('start','enter','exit','recount','pause','resume','end','gap')),
 delta INTEGER NOT NULL, occupancy INTEGER NOT NULL CHECK(occupancy>=0)
);
CREATE INDEX field_event_session ON field_event(session_id,id);
