"""Tests for the user data access layer."""

import sqlite3

import pytest

import models.user as user_module


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    """Point the module at a fresh in-memory database for each test."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(user_module, "DB_PATH", db_file)
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE)"
    )
    conn.commit()
    conn.close()


def test_create_and_get_user():
    uid = user_module.create_user("alice", "alice@example.com")
    assert uid is not None
    user = user_module.get_user(uid)
    assert user["username"] == "alice"


def test_get_user_missing():
    assert user_module.get_user(9999) is None


def test_list_users():
    user_module.create_user("bob", "bob@example.com")
    user_module.create_user("carol", "carol@example.com")
    users = user_module.list_users()
    assert len(users) == 2
