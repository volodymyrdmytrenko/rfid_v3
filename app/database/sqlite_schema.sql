CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    rfid TEXT UNIQUE,
    full_name TEXT,
    full_name_norm TEXT,
    active INTEGER DEFAULT 1,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_employees_full_name_norm ON employees(full_name_norm);
CREATE INDEX IF NOT EXISTS idx_employees_full_name ON employees(full_name);


CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER,
    visit_time TEXT,
    source TEXT,
    synced INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_visits_time ON visits(visit_time);

CREATE UNIQUE INDEX IF NOT EXISTS ux_visits_employee_day
ON visits(employee_id, date(visit_time));

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  operator_id INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

