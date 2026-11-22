CREATE TABLE campus_demo_purchase (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    product TEXT NOT NULL,
    cost INTEGER NOT NULL CHECK (cost>0),
    state TEXT NOT NULL CHECK (state IN ('pending','paid','refunded','cancelled')),
    created_at TEXT NOT NULL,
    paid_at TEXT,
    closed_at TEXT,
    CHECK ((state='pending' AND paid_at IS NULL AND closed_at IS NULL)
        OR (state='paid' AND paid_at IS NOT NULL AND closed_at IS NULL)
        OR (state='refunded' AND paid_at IS NOT NULL AND closed_at IS NOT NULL)
        OR (state='cancelled' AND paid_at IS NULL AND closed_at IS NOT NULL)),
    FOREIGN KEY (university_id,owner_id) REFERENCES account(university_id,id)
);

CREATE INDEX campus_demo_purchase_owner ON campus_demo_purchase(university_id,owner_id,state);
