import os
import json
import sqlite3
import asyncio
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")
ADMIN_IDS = {465313785, 1935484494}

if not TOKEN:
    raise RuntimeError("TOKEN is not set")

DB_NAME = "quiz.db"

# =========================
# KEEP ALIVE (RENDER)
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
# DB
# =========================

conn = sqlite3.connect(DB_NAME, check_same_thread=False)
conn.row_factory = sqlite3.Row


def db():
    return conn


def init_db():
    cur = db().cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS quizzes (
        name TEXT PRIMARY KEY
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz_name TEXT,
        question TEXT,
        options TEXT,
        answer TEXT,
        photo TEXT,
        position INTEGER DEFAULT 0
    )
    """)

    conn.commit()


init_db()

# =========================
# STATE
# =========================

USER_STATE = {}
ADMIN_STATE = {}

# =========================
# HELPERS
# =========================

def is_admin(uid):
    return uid in ADMIN_IDS


def get_quizzes():
    cur = db().cursor()
    cur.execute("SELECT name FROM quizzes")
    return [r["name"] for r in cur.fetchall()]


def get_questions(quiz):
    cur = db().cursor()
    cur.execute("SELECT * FROM questions WHERE quiz_name=? ORDER BY position", (quiz,))
    rows = cur.fetchall()

    return [
        {
            "id": r["id"],
            "question": r["question"],
            "options": json.loads(r["options"]),
            "answer": r["answer"],
            "photo": r["photo"],
        }
        for r in rows
    ]


def norm(t):
    return (t or "").strip().lower()

# =========================
# ADMIN
# =========================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("❌ Нет доступа")
        return

    ADMIN_STATE[uid] = {"step": "quiz_name"}

    await update.message.reply_text("🛠 Введите название викторины")


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ADMIN_STATE.pop(uid, None)
    await update.message.reply_text("✅ Админка завершена")


async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if uid not in ADMIN_STATE:
        return

    state = ADMIN_STATE[uid]

    if state["step"] == "quiz_name":
        state["quiz"] = text

        db().execute("INSERT OR IGNORE INTO quizzes(name) VALUES(?)", (text,))
        conn.commit()

        state["step"] = "question"
        await update.message.reply_text("Вопрос? (/done чтобы выйти)")
        return

    if state["step"] == "question":
        state["question"] = text
        state["step"] = "options"
        await update.message.reply_text("Варианты через запятую")
        return

    if state["step"] == "options":
        state["options"] = [x.strip() for x in text.split(",")]
        state["step"] = "answer"
        await update.message.reply_text("Правильный ответ")
        return

    if state["step"] == "answer":
        db().execute("""
            INSERT INTO questions(quiz_name, question, options, answer)
            VALUES (?, ?, ?, ?)
        """, (
            state["quiz"],
            state["question"],
            json.dumps(state["options"], ensure_ascii=False),
            text
        ))
        conn.commit()

        state["step"] = "question"
        await update.message.reply_text("✔ Добавлено. Следующий вопрос или /done")

# =========================
# USER
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Напиши название викторины")


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if uid in ADMIN_STATE:
        await admin_flow(update, context)
        return

    quizzes = get_quizzes()

    if text in quizzes:
        USER_STATE[uid] = {"quiz": text, "q": 0, "score": 0}
        await send_question(update)
        return

    if uid in USER_STATE:
        await handle_answer(update)
        return

    await update.message.reply_text("Выбери викторину")


async def send_question(update: Update):
    uid = update.effective_user.id
    state = USER_STATE[uid]

    qs = get_questions(state["quiz"])

    if state["q"] >= len(qs):
        await update.message.reply_text(f"🎉 Готово! {state['score']}/{len(qs)}")
        USER_STATE.pop(uid)
        return

    q = qs[state["q"]]

    kb = ReplyKeyboardMarkup([[o] for o in q["options"]], resize_keyboard=True)

    state["answer"] = q["answer"]

    await update.message.reply_text(q["question"], reply_markup=kb)


async def handle_answer(update: Update):
    uid = update.effective_user.id
    state = USER_STATE[uid]

    text = norm(update.message.text)
    correct = norm(state["answer"])

    if text == correct:
        state["score"] += 1
        await update.message.reply_text("✅ верно")
    else:
        await update.message.reply_text(f"❌ неверно\nОтвет: {state['answer']}")

    state["q"] += 1
    await send_question(update)

# =========================
# MAIN
# =========================

def main():
    Thread(target=run_web, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("done", done))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("BOT RUNNING 🚀")

    # 🔥 ВАЖНО: фикс asyncio для Render/Python 3.13+
    asyncio.run(app.run_polling(close_loop=False))


if __name__ == "__main__":
    main()
