CREATE TABLE step_cache (
  step_key TEXT NOT NULL CHECK (length(step_key) = 64),
  dir TEXT NOT NULL,
  kind TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('calling', 'ready')),
  sha256 TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
  bytes INTEGER CHECK (bytes IS NULL OR bytes >= 0),
  created_at TEXT NOT NULL,
  used_at TEXT NOT NULL,
  PRIMARY KEY (step_key, dir)
) STRICT;

CREATE INDEX step_cache_by_use ON step_cache (used_at);
