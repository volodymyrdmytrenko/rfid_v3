import mysql.connector
from app.utils.config import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB


def get_mysql_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        autocommit=True,
        connection_timeout=5
    )