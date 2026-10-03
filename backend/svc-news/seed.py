"""Seed data/news.db with 5 posts. Stdlib only."""
import os
import sqlite3
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "news.db")

POSTS = [
    ("Ship v1: portfolio backend online", "First release of the news service with SQLite storage and REST API."),
    ("How this API works", "GET /api/news lists posts, POST /api/news creates one. JSON only."),
    ("SQLite over ORM", "No dependencies: sqlite3 + http.server keeps the service portable."),
    ("CORS enabled", "Any frontend origin can read and post during development."),
    ("What's next", "Pagination, search, and per-post GET are easy follow-ups."),
]


def main():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS posts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL)""")
    if c.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0:
        now = datetime.now(timezone.utc).isoformat()
        c.executemany("INSERT INTO posts (title,body,created) VALUES (?,?,?)",
                      [(t, b, now) for t, b in POSTS])
        c.commit()
        print(f"seeded {len(POSTS)} posts -> {DB}")
    else:
        print("already seeded, skipping")
    c.close()


if __name__ == "__main__":
    main()
