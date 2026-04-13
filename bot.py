import os
import json
import sqlite3

from fastapi import FastAPI, Request
from telegram import Update, ReplyKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-service.onrender.com
ADMIN_IDS = {465313785, 1935484494}

if not TOKEN:
    raise RuntimeError("TOKEN is not set")

bot = Bot(token=TOKEN)
app_fastapi = FastAPI()

# =========================
# DB
# =========================

conn = sqlite3.connect("quiz.db", check_same_thread=False)
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
        answer TEXT
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
    cur.execute("SELECT * FROM questions WHERE quiz_name=?", (quiz,))
    rows = cur.fetchall()

    return [
        {
            "question": r["question"],
            "options": json.loads(r["options"]),
            "answer": r["answer"],
        }
        for r in rows
    ]


def norm(t):
    return (t or "").strip().lower()

# =========================
# HANDLERS
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
# ADMIN (упрощённо)
# =========================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("❌ нет доступа")
        return

    ADMIN_STATE[uid] = {"step": "quiz"}
    await update.message.reply_text("🛠 название викторины")


async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    state = ADMIN_STATE[uid]

    if state["step"] == "quiz":
        state["quiz"] = text
        db().execute("INSERT OR IGNORE INTO quizzes(name) VALUES(?)", (text,))
        conn.commit()

        state["step"] = "question"
        await update.message.reply_text("вопрос?")
        return

    if state["step"] == "question":
        state["question"] = text
        state["step"] = "options"
        await update.message.reply_text("варианты через запятую")
        return

    if state["step"] == "options":
        state["options"] = [x.strip() for x in text.split(",")]
        state["step"] = "answer"
        await update.message.reply_text("правильный ответ")
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
        await update.message.reply_text("✔ добавлено")

# =========================
# FASTAPI WEBHOOK
# =========================

@app_fastapi.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot)

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    await application.process_update(update)
    return {"ok": True}

# =========================
# SET WEBHOOK ON START
# =========================

@app_fastapi.on_event("startup")
async def startup():
    await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    print("WEBHOOK SET 🚀")
