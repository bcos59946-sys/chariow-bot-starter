import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("CHARIOW_BOT_DB", "runtime/chariow_bot.sqlite3"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as db:
        db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          telegram_user_id INTEGER NOT NULL,
          product_id TEXT NOT NULL,
          chariow_sale_id TEXT,
          checkout_url TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          currency TEXT,
          amount INTEGER,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(chariow_sale_id)
        )
        """)
        db.execute("""
        CREATE TABLE IF NOT EXISTS pulse_deliveries (
          delivery_id TEXT PRIMARY KEY,
          sale_id TEXT,
          event TEXT,
          received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)

def create_order(user_id: int, product_id: str, sale_id: str | None, checkout_url: str | None,
                 currency: str | None, amount: int | None) -> int:
    with connect() as db:
        cur = db.execute("""
        INSERT INTO orders (telegram_user_id, product_id, chariow_sale_id, checkout_url, status, currency, amount)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (user_id, product_id, sale_id, checkout_url, currency, amount))
        return int(cur.lastrowid)

def recent_orders(user_id: int, limit: int = 10):
    with connect() as db:
        return db.execute("""
        SELECT id, product_id, chariow_sale_id, status, currency, amount, created_at
        FROM orders WHERE telegram_user_id=? ORDER BY id DESC LIMIT ?
        """, (user_id, limit)).fetchall()

def recent_sales(limit: int = 30):
    with connect() as db:
        return db.execute("""
        SELECT id, telegram_user_id, product_id, chariow_sale_id, status, currency, amount, created_at
        FROM orders ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
