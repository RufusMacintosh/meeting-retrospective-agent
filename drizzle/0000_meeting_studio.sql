CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at TEXT NOT NULL);
--> statement-breakpoint
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at TEXT NOT NULL);
--> statement-breakpoint
CREATE INDEX sessions_user ON sessions(user_id);
--> statement-breakpoint
CREATE TABLE auth_attempts (attempt_key TEXT PRIMARY KEY, count INTEGER NOT NULL, window_start TEXT NOT NULL);
--> statement-breakpoint
CREATE TABLE projects (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
--> statement-breakpoint
CREATE INDEX projects_user ON projects(user_id);
--> statement-breakpoint
CREATE TABLE meetings (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, meeting_date TEXT NOT NULL, transcript TEXT NOT NULL, analysis TEXT, provider TEXT, model TEXT, created_at TEXT NOT NULL, analyzed_at TEXT);
--> statement-breakpoint
CREATE INDEX meetings_project ON meetings(project_id,created_at);
--> statement-breakpoint
CREATE TABLE matters (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, module TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL, owner TEXT, due_date TEXT, description TEXT NOT NULL, evidence TEXT NOT NULL, revision_count INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
--> statement-breakpoint
CREATE INDEX matters_project ON matters(project_id,updated_at);
--> statement-breakpoint
CREATE TABLE revisions (id TEXT PRIMARY KEY, matter_id TEXT NOT NULL REFERENCES matters(id) ON DELETE CASCADE, meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE, snapshot TEXT NOT NULL, created_at TEXT NOT NULL);
--> statement-breakpoint
CREATE INDEX revisions_matter ON revisions(matter_id,created_at);
--> statement-breakpoint
CREATE TABLE provider_keys (user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, provider TEXT NOT NULL, cipher TEXT NOT NULL, iv TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(user_id,provider));
