CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    rfid TEXT UNIQUE,
    full_name TEXT NOT NULL,
    full_name_norm TEXT,
    fmoney INTEGER NOT NULL DEFAULT 50,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_employees_full_name_norm ON employees(full_name_norm);
CREATE INDEX IF NOT EXISTS idx_employees_full_name ON employees(full_name);

CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    visit_time TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('rfid', 'manual')),
    synced INTEGER NOT NULL DEFAULT 0 CHECK (synced IN (0, 1)),
    sync_uuid TEXT NOT NULL UNIQUE,
    FOREIGN KEY (employee_id) REFERENCES employees(id)
);

CREATE UNIQUE INDEX ux_visits_employee_day
ON visits(employee_id, date(visit_time));
CREATE UNIQUE INDEX ux_visits_sync_uuid ON visits(sync_uuid);
CREATE INDEX ix_visits_visit_time ON visits(visit_time);
CREATE INDEX ix_visits_synced ON visits(synced);
CREATE INDEX ix_visits_employee_visit_time ON visits(employee_id, visit_time);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    operator_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);