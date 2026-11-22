CREATE INDEX login_attempt_address_time ON login_attempt(address, attempted_at);
CREATE INDEX session_expiry ON session(expires_at);
