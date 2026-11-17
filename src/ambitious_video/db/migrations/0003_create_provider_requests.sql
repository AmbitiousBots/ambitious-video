CREATE TABLE provider_requests (
  step_key TEXT PRIMARY KEY CHECK (length(step_key) = 64),
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  request_id TEXT NOT NULL CHECK (length(request_id) BETWEEN 1 AND 200),
  created_at TEXT NOT NULL
) STRICT;
