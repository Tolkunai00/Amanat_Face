from school_face_id.db.connection import _postgres_sql


def test_postgres_sql_translates_parameters_and_case_insensitive_match():
    sql = _postgres_sql("SELECT id FROM parent_accounts WHERE login = ? COLLATE NOCASE")
    assert sql == "SELECT id FROM parent_accounts WHERE LOWER(login) = LOWER(%s)"


def test_postgres_sql_translates_insert_or_ignore():
    sql = _postgres_sql("INSERT OR IGNORE INTO classes (id, name) VALUES (?, ?)")
    assert sql == "INSERT INTO classes (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING"


def test_postgres_sql_translates_sqlite_dates():
    sql = _postgres_sql("SELECT date('now', 'localtime'), date(a.timestamp), time(a.timestamp)")
    assert "CURRENT_DATE" in sql
    assert "CAST(a.timestamp AS timestamp)::date" in sql
    assert "CAST(a.timestamp AS timestamp)::time" in sql


def test_postgres_sql_skips_sqlite_sequence_reset():
    assert _postgres_sql("DELETE FROM sqlite_sequence WHERE name = 'attendance'") is None
