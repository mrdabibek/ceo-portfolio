#!/usr/bin/env python3
"""Seed 10 portfolio projects."""
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "portfolio.db")

ROWS = [
    ("saas-starter", "SaaS Starter Landing", "saas", 49, "https://demo.local/saas-starter"),
    ("shop-fashion", "Fashion Shop Template", "shop", 79, "https://demo.local/shop-fashion"),
    ("chat-app", "Realtime Chat App", "chat", 99, "https://demo.local/chat-app"),
    ("banking-mobile", "Mobile Banking UI", "banking", 129, "https://demo.local/banking-mobile"),
    ("win11-clone", "Windows 11 Web Clone", "win", 0, "https://demo.local/win11-clone"),
    ("macos-clone", "macOS Web Clone", "macos", 0, "https://demo.local/macos-clone"),
    ("racer-game", "2D Racer Game", "game", 29, "https://demo.local/racer-game"),
    ("ai-chatbot", "AI Chatbot Widget", "ai", 149, "https://demo.local/ai-chatbot"),
    ("ai-automation", "AI Automation Pipeline", "ai", 199, "https://demo.local/ai-automation"),
    ("saas-analytics", "SaaS Analytics Dashboard", "saas", 119, "https://demo.local/saas-analytics"),
]

os.makedirs(os.path.dirname(DB), exist_ok=True)
c = sqlite3.connect(DB)
c.execute(
    "CREATE TABLE IF NOT EXISTS projects"
    "(slug TEXT PRIMARY KEY,title TEXT NOT NULL,cat TEXT NOT NULL,"
    "price REAL NOT NULL DEFAULT 0,demo TEXT NOT NULL DEFAULT '')"
)
c.executemany("INSERT OR REPLACE INTO projects(slug,title,cat,price,demo) VALUES(?,?,?,?,?)", ROWS)
c.commit()
n = c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
c.close()
print(f"seeded {n} projects -> {DB}")
