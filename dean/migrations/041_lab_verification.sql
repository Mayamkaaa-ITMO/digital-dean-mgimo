CREATE TABLE lab_purchase_method (
    purchase_id TEXT PRIMARY KEY REFERENCES campus_demo_purchase(id),
    method TEXT NOT NULL CHECK(method IN ('nfc','demo','qr'))
);
ALTER TABLE lab_lecture ADD COLUMN analysis_token TEXT;
ALTER TABLE lab_lecture ADD COLUMN analysis_status TEXT;
ALTER TABLE lab_lecture ADD COLUMN analysis_error TEXT;
