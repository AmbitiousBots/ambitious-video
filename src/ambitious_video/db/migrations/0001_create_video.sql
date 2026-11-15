CREATE TABLE jobs (
  id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 64),
  script_sha256 TEXT NOT NULL CHECK (length(script_sha256) = 64),
  state TEXT NOT NULL CHECK (
    state IN ('queued', 'rendering', 'rendered', 'verified', 'failed', 'retried')
  ),
  created_at TEXT NOT NULL,
  finished_at TEXT,
  output_path TEXT NOT NULL,
  output_sha256 TEXT,
  script_json TEXT NOT NULL,
  script_dir TEXT NOT NULL,
  duration_sec TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  fps INTEGER NOT NULL
) STRICT;

CREATE TABLE ledger (
  id INTEGER PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs (id),
  step TEXT NOT NULL,
  step_index INTEGER NOT NULL CHECK (step_index >= 0),
  units TEXT NOT NULL,
  usd TEXT NOT NULL,
  idem_key TEXT NOT NULL UNIQUE
) STRICT;

CREATE INDEX ledger_by_job ON ledger (job_id);

CREATE TABLE assets (
  sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
  path TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (
    kind IN ('image', 'video', 'audio', 'voiceover', 'screen_recording')
  )
) STRICT;
