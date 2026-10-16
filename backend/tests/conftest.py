import pytest

from school_face_id.data import database
from school_face_id.db import connection, migrations


@pytest.fixture(scope="session", autouse=True)
def isolated_database(tmp_path_factory):
    original_name = database.DB_NAME
    original_test_mode = connection.SQLITE_TEST_MODE
    test_name = str(tmp_path_factory.mktemp("database") / "test.db")
    database.DB_NAME = test_name
    connection.DB_NAME = test_name
    migrations.DB_NAME = test_name
    connection.SQLITE_TEST_MODE = True
    database.init_db()
    try:
        yield
    finally:
        database.DB_NAME = original_name
        connection.DB_NAME = original_name
        migrations.DB_NAME = original_name
        connection.SQLITE_TEST_MODE = original_test_mode
