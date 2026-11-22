CREATE TABLE finance_award (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('stipend','work','support')),
    title TEXT NOT NULL,
    period TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount>0),
    assigned_at TEXT NOT NULL,
    source_app TEXT UNIQUE REFERENCES application(id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,group_id) REFERENCES study_group(university_id,id)
);
CREATE INDEX finance_award_owner ON finance_award(university_id,student_id);
CREATE TABLE finance_disbursement (
    award_id TEXT PRIMARY KEY REFERENCES finance_award(id),
    amount INTEGER NOT NULL CHECK(amount>0),
    paid_at TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES account(id)
);
CREATE TABLE tuition_charge (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('tuition','residence')),
    title TEXT NOT NULL,
    period TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount>0),
    due_at TEXT NOT NULL,
    UNIQUE(university_id,id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id)
);
CREATE INDEX tuition_charge_owner ON tuition_charge(university_id,student_id,due_at);
CREATE TABLE demo_payment (
    id TEXT PRIMARY KEY,
    charge_id TEXT NOT NULL REFERENCES tuition_charge(id),
    state TEXT NOT NULL CHECK(state IN ('success','failed')),
    amount INTEGER NOT NULL CHECK(amount>0),
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX demo_payment_once ON demo_payment(charge_id) WHERE state='success';
CREATE INDEX demo_payment_charge ON demo_payment(charge_id,created_at);
CREATE TABLE finance_visibility (
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    private INTEGER NOT NULL CHECK(private IN (0,1)),
    PRIMARY KEY(university_id,student_id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id)
);

CREATE TABLE residence_hall (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university(id),
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    rules TEXT NOT NULL,
    contact TEXT NOT NULL,
    UNIQUE(university_id,id)
);
CREATE TABLE residence_stay (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    hall_id TEXT NOT NULL,
    room TEXT NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    CHECK(starts_on<=ends_on),
    FOREIGN KEY(university_id,hall_id) REFERENCES residence_hall(university_id,id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id)
);
CREATE INDEX residence_owner ON residence_stay(university_id,student_id);

CREATE TABLE budget_entry (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('income','expense')),
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount>0),
    entry_date TEXT NOT NULL,
    reminder_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id)
);
CREATE INDEX budget_entry_owner ON budget_entry(university_id,student_id,entry_date);
