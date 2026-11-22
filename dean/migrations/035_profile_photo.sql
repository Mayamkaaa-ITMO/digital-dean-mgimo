CREATE TABLE profile_photo (
    owner_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    media_type TEXT NOT NULL CHECK (media_type IN ('image/jpeg', 'image/png')),
    content BLOB NOT NULL CHECK (length(content) BETWEEN 1 AND 2097152)
);
