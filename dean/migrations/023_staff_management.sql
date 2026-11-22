CREATE TABLE staff_instructor_manager (
    account_id TEXT PRIMARY KEY REFERENCES account(id)
);

CREATE TABLE managed_service (
    university_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES account(id),
    department TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    form_json TEXT NOT NULL,
    PRIMARY KEY (university_id,service_id),
    FOREIGN KEY (university_id,service_id) REFERENCES service(university_id,id),
    FOREIGN KEY (university_id,group_id) REFERENCES study_group(university_id,id)
);

CREATE TABLE managed_service_revision (
    university_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    actor_id TEXT NOT NULL REFERENCES account(id),
    snapshot_json TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (university_id,service_id,version),
    FOREIGN KEY (university_id,service_id) REFERENCES managed_service(university_id,service_id)
);

CREATE TABLE managed_offer (
    bookable_id TEXT PRIMARY KEY REFERENCES bookable(id),
    group_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('draft','published','retired')),
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE managed_equipment (
    unit_id TEXT PRIMARY KEY REFERENCES equipment_unit(id),
    group_id TEXT NOT NULL
);

CREATE TABLE managed_offer_history (
    id INTEGER PRIMARY KEY,
    bookable_id TEXT NOT NULL REFERENCES managed_offer(bookable_id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    version INTEGER NOT NULL,
    action TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(bookable_id,version)
);
