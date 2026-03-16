from app.database.sqlite_db import get_connection

conn = get_connection()
cur = conn.cursor()

print("\n--- EMPLOYEES ---")
cur.execute("SELECT id, rfid, last_name, first_name, active FROM employees LIMIT 20")
for row in cur.fetchall():
    print(dict(row))

print("\n--- VISITS ---")
cur.execute("SELECT id, employee_id, visit_time, synced FROM visits ORDER BY id DESC LIMIT 20")
for row in cur.fetchall():
    print(dict(row))

conn.close()
