CREATE TABLE activity_room (
    bookable_id TEXT PRIMARY KEY REFERENCES bookable(id),
    place_key TEXT NOT NULL
);

CREATE TABLE activity_reminder (
    reservation_id TEXT PRIMARY KEY REFERENCES reservation(id)
);
