import psycopg

from core.contracts.configuration import DatabaseSettings


def check_postgres(settings: DatabaseSettings) -> bool:
    with psycopg.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.user,
        password=settings.password,
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0] == 1
