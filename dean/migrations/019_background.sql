CREATE TABLE reminder_job (
    owner_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    available_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts>=0),
    lease_id TEXT,
    lease_until TEXT,
    last_started_at TEXT,
    last_success_at TEXT,
    error_code TEXT,
    CHECK ((lease_id IS NULL) = (lease_until IS NULL))
);
CREATE INDEX reminder_job_due ON reminder_job(available_at,lease_until);

CREATE TABLE reminder_worker_clock (
    slot INTEGER PRIMARY KEY CHECK (slot=1),
    observed_at TEXT NOT NULL
);

CREATE TABLE public_rollback (
    preview_id TEXT PRIMARY KEY REFERENCES public_preview(id),
    restore_preview_id TEXT NOT NULL REFERENCES public_preview(id),
    requested_by TEXT NOT NULL REFERENCES account(id),
    requested_at TEXT NOT NULL,
    approved_by TEXT REFERENCES account(id),
    applied_at TEXT
);
