from app.database.sqlite_db import get_connection
from app.database.mysql_db import get_mysql_connection

print("Checking SQLite...")
s = get_connection()
sc = s.cursor()
sc.execute("SELECT COUNT(*) c FROM employees")
print("SQLite employees:", sc.fetchone()["c"])

print("Checking MySQL...")
m = get_mysql_connection()
mc = m.cursor(dictionary=True)
mc.execute("SELECT COUNT(*) c FROM employees")
print("MySQL employees:", mc.fetchone()["c"])
