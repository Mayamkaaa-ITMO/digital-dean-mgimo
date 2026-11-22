CREATE TABLE assistant_conversation (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (id, owner_id)
);

CREATE INDEX assistant_conversation_owner_updated ON assistant_conversation(owner_id, updated_at DESC);

CREATE TABLE assistant_message (
    owner_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    question TEXT NOT NULL,
    reply_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, command_id),
    FOREIGN KEY (conversation_id, owner_id) REFERENCES assistant_conversation(id, owner_id) ON DELETE CASCADE
);

CREATE INDEX assistant_message_thread ON assistant_message(conversation_id, created_at);
