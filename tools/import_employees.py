import pandas as pd
import mysql.connector
import sys

MYSQL_HOST = "localhost"
MYSQL_USER = "rfid"
MYSQL_PASSWORD = "rfidpass"
MYSQL_DB = "canteen"


def main(file_path):
    df = pd.read_excel(file_path)

    conn = mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB
    )
    cur = conn.cursor()

    cur.execute("DELETE FROM employees")

    for _, row in df.iterrows():
        cur.execute("""
            INSERT INTO employees (id, rfid, last_name, first_name, middle_name, active)
            VALUES (%s, %s, %s, %s, %s, 1)
        """, (
            int(row["id"]),
            str(row["rfid"]),
            row["last_name"],
            row["first_name"],
            row.get("middle_name", "")
        ))

    conn.commit()
    conn.close()

    print("Import completed.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python import_employees.py file.xlsx")
        exit(1)
    main(sys.argv[1])
