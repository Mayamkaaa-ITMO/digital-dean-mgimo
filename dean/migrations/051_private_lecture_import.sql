ALTER TABLE lab_lecture ADD COLUMN visibility TEXT NOT NULL DEFAULT 'group'
    CHECK (visibility IN ('group', 'private'));
