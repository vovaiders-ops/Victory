import os
import asyncio
import json
import sqlite3
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("❌ TOKEN is not set in environment variables")

ADMIN_IDS = {465313785, 1935484494}
TIME_LIMIT = 20
DB_NAME = "quiz.db"

SOCIAL_TG = "https://t.me/videt_i_slyshat"
SOCIAL_VK = "https://vk.com/art_in_church"

# =========================
# KEEP ALIVE (Render 24/7 FIX)
# =========================

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_web():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

# =========================
# SQLITE FAST CONNECTION
# =========================

DB_CONN = sqlite3.connect(DB_NAME, check_same_thread=False)
DB_CONN.row_factory = sqlite3.Row

def db():
    return DB_CONN

# =========================
# INIT DB (FULL STRUCTURE)
# =========================

def init_db():
    cur = db().cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS quizzes (
        name TEXT PRIMARY KEY,
        description TEXT DEFAULT '',
        starts INTEGER DEFAULT 0,
        completions INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_name TEXT NOT NULL,
        question TEXT NOT NULL,
        options TEXT NOT NULL,
        answer TEXT NOT NULL,
        photo TEXT,
        position INTEGER DEFAULT 0,
        shown INTEGER DEFAULT 0,
        correct INTEGER DEFAULT 0,
        wrong INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        quiz_name TEXT,
        score INTEGER,
        total INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        user_name TEXT,
        full_name TEXT,
        last_seen TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    db().commit()

init_db()

# =========================
# MEMORY STATE
# =========================

USER_STATE = {}
ADMIN_STATE = {}
ACTIVE_TIMERS = {}

# =========================
# HELPERS
# =========================

def is_admin(uid):
    return uid in ADMIN_IDS


def normalize(t):
    return (t or "").strip().casefold()


def cancel_timer(uid):
    task = ACTIVE_TIMERS.get(uid)
    if task:
        task.cancel()
    ACTIVE_TIMERS.pop(uid, None)


def register_user(user):
    cur = db().cursor()
    cur.execute("""
        INSERT INTO users(user_id, user_name, full_name, last_seen)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            user_name=excluded.user_name,
            full_name=excluded.full_name,
            last_seen=CURRENT_TIMESTAMP
    """, (user.id, user.username or user.first_name, user.full_name))
    db().commit()

# =========================
# QUIZ CORE (OPTIMIZED)
# =========================

def get_quizzes():
    cur = db().cursor()
    cur.execute("SELECT * FROM quizzes ORDER BY name COLLATE NOCASE")
    return cur.fetchall()


def get_quiz_names():
    return [q["name"] for q in get_quizzes()]


def get_questions(quiz):
    cur = db().cursor()
    cur.execute("""
        SELECT * FROM questions
        WHERE quiz_name=?
        ORDER BY position, id
    """, (quiz,))
    rows = cur.fetchall()

    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "question": r["question"],
            "options": json.loads(r["options"]),
            "answer": r["answer"],
            "photo": r["photo"],
            "shown": r["shown"],
            "correct": r["correct"],
            "wrong": r["wrong"],
        })
    return out


def get_question(quiz, idx):
    qs = get_questions(quiz)
    if 0 <= idx < len(qs):
        return qs[idx]
    return None


def add_question(quiz, q, opts, ans, photo):
    cur = db().cursor()
    cur.execute("""
        SELECT COALESCE(MAX(position),0)+1 FROM questions WHERE quiz_name=?
    """, (quiz,))
    pos = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO questions(quiz_name, question, options, answer, photo, position)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (quiz, q, json.dumps(opts, ensure_ascii=False), ans, photo, pos))
    db().commit()

# =========================
# KEEP ALIVE SERVER
# =========================

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")


def run_web():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

# =========================
# BOT CORE
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    await update.message.reply_text(
        "👋 Викторина запущена\n\n"
        "⚡ Render 24/7 активен\n"
        "🎯 База загружена\n\n"
        "Напиши название викторины"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()

    register_user(update.effective_user)

    quiz_names = get_quiz_names()

    # выбор викторины
    if text in quiz_names:
        USER_STATE[uid] = {
            "quiz": text,
            "q": 0,
            "score": 0,
            "mode": "play"
        }
        await send_question(update, context)
        return

    # обработка ответа
    if USER_STATE.get(uid, {}).get("mode") == "play":
        await handle_answer(update, context)
        return

    await update.message.reply_text("Выбери викторину из списка")


# =========================
# QUIZ FLOW
# =========================

async def send_question(update, context):
    uid = update.effective_user.id
    state = USER_STATE.get(uid)

    if not state:
        return

    quiz = state["quiz"]
    idx = state["q"]

    qs = get_questions(quiz)

    if idx >= len(qs):
        await update.message.reply_text(
            f"🎉 Викторина завершена!\nРезультат: {state['score']}/{len(qs)}"
        )
        USER_STATE.pop(uid, None)
        return

    q = qs[idx]

    keyboard = ReplyKeyboardMarkup([[o] for o in q["options"]], resize_keyboard=True)

    state["current_answer"] = q["answer"]
    state["current_id"] = q["id"]

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=keyboard)
    else:
        await update.message.reply_text(q["question"], reply_markup=keyboard)


async def handle_answer(update, context):
    uid = update.effective_user.id
    state = USER_STATE.get(uid)

    if not state:
        return

    text = normalize(update.message.text)
    correct = normalize(state.get("current_answer"))

    if text == correct:
        state["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно\nОтвет: {state['current_answer']}")

    state["q"] += 1
    await send_question(update, context)

# =========================
# MAIN
# =========================

def main():
    Thread(target=run_web, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🤖 FINAL BOT RUNNING...")
    app.run_polling()


if __name__ == "__main__":
    main()
