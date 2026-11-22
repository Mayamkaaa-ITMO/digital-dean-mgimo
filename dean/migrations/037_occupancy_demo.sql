CREATE TABLE demo_room_flow (
    room_key TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    entered INTEGER NOT NULL CHECK (entered>=0),
    exited INTEGER NOT NULL CHECK (exited>=0),
    PRIMARY KEY (room_key,observed_at)
);
