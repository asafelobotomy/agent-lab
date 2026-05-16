# App

Small Flask application backed by a local SQLite database via direct `sqlite3` calls.

## Setup

```bash
pip install -r requirements.txt
python db.py        # initialise database
```

## Structure

```
db.py            database initialisation
models/user.py   user data access (sqlite3)
tests/           pytest suite
```
