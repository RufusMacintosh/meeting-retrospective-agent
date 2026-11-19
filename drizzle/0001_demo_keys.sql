CREATE TABLE demo_provider_keys (
  session_hash TEXT NOT NULL REFERENCES sessions(token_hash) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  cipher TEXT NOT NULL,
  iv TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(session_hash, provider)
);
