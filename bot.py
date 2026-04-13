# QUIZ BOT PRO SQLITE
# Stable product version with:
# - quizzes with descriptions
# - photo questions
# - add / edit / delete / reorder questions
# - delete whole quiz
# - stats for quizzes and questions
# - top-10
# - admin buttons

import asyncio
import json
import sqlite3
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

TOKEN = "8470983799:AAG-Z0XvVueXpKaDgPvFKhtPUBtukfZpDOE"
ADMIN_IDS = {465313785, 1935484494}
TIME_LIMIT = 20
DB_NAME = "quiz.db"
SOCIAL_TG = "https://t.me/videt_i_slyshat"
SOCIAL_VK = "https://vk.com/art_in_church"


# =========================
# DATABASE
# =========================
def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

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
        user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,
        quiz_name TEXT NOT NULL,
        score INTEGER NOT NULL,
        total INTEGER NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        user_name TEXT NOT NULL,
        full_name TEXT NOT NULL,
        started_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_seen TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


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


def normalize(text: str) -> str:
    return (text or "").strip().casefold()


def cancel_timer(uid: int):
    task = ACTIVE_TIMERS.get(uid)
    if task and not task.done():
        task.cancel()
    ACTIVE_TIMERS.pop(uid, None)


def register_user(user):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users(user_id, user_name, full_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            user_name=excluded.user_name,
            full_name=excluded.full_name,
            last_seen=CURRENT_TIMESTAMP
        """,
        (user.id, user.username or user.first_name or str(user.id), user.full_name),
    )
    conn.commit()
    conn.close()


def get_quizzes():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT name, description, starts, completions FROM quizzes ORDER BY name COLLATE NOCASE")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_quiz_names():
    return [row["name"] for row in get_quizzes()]


def get_quiz_by_name(name: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT name, description, starts, completions FROM quizzes WHERE name=?", (name,))
    row = cur.fetchone()
    conn.close()
    return row


def get_quiz_name_by_index(idx: int):
    quizzes = get_quizzes()
    if 0 <= idx < len(quizzes):
        return quizzes[idx]["name"]
    return None


def create_quiz(name: str, description: str = ""):
    conn = db()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO quizzes(name, description) VALUES (?, ?)", (name, description))
    conn.commit()
    conn.close()


def update_quiz_description(name: str, description: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE quizzes SET description=? WHERE name=?", (description, name))
    conn.commit()
    conn.close()


def increment_quiz_start(name: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE quizzes SET starts = starts + 1 WHERE name=?", (name,))
    conn.commit()
    conn.close()


def increment_quiz_completion(name: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE quizzes SET completions = completions + 1 WHERE name=?", (name,))
    conn.commit()
    conn.close()


def delete_quiz(name: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM questions WHERE quiz_name=?", (name,))
    cur.execute("DELETE FROM results WHERE quiz_name=?", (name,))
    cur.execute("DELETE FROM quizzes WHERE name=?", (name,))
    conn.commit()
    conn.close()


def get_questions(quiz_name: str):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, quiz_name, question, options, answer, photo, position, shown, correct, wrong
        FROM questions
        WHERE quiz_name=?
        ORDER BY position ASC, id ASC
        """,
        (quiz_name,),
    )
    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        try:
            options = json.loads(row["options"]) if row["options"] else []
        except:
            options = [x.strip() for x in (row["options"] or "").split(",") if x.strip()]
        result.append({
            "id": row["id"],
            "quiz_name": row["quiz_name"],
            "question": row["question"],
            "options": options,
            "answer": row["answer"],
            "photo": row["photo"],
            "position": row["position"],
            "shown": row["shown"],
            "correct": row["correct"],
            "wrong": row["wrong"],
        })
    return result


def get_question(quiz_name: str, index: int):
    questions = get_questions(quiz_name)
    if 0 <= index < len(questions):
        return questions[index]
    return None


def get_question_count(quiz_name: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM questions WHERE quiz_name=?", (quiz_name,))
    c = cur.fetchone()[0]
    conn.close()
    return c


def add_question(quiz_name: str, question: str, options, answer: str, photo: str | None):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next_pos FROM questions WHERE quiz_name=?", (quiz_name,))
    pos = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO questions(quiz_name, question, options, answer, photo, position)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (quiz_name, question, json.dumps(options, ensure_ascii=False), answer, photo, pos),
    )
    conn.commit()
    conn.close()


def update_question_field(question_id: int, field: str, value):
    if field not in {"question", "options", "answer", "photo"}:
        return
    conn = db()
    cur = conn.cursor()
    cur.execute(f"UPDATE questions SET {field}=? WHERE id=?", (value, question_id))
    conn.commit()
    conn.close()


def swap_question_positions(quiz_name: str, idx1: int, idx2: int):
    questions = get_questions(quiz_name)
    if not (0 <= idx1 < len(questions) and 0 <= idx2 < len(questions)):
        return False
    if idx1 == idx2:
        return True

    q1 = questions[idx1]
    q2 = questions[idx2]

    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE questions SET position=? WHERE id=?", (q2["position"], q1["id"]))
    cur.execute("UPDATE questions SET position=? WHERE id=?", (q1["position"], q2["id"]))
    conn.commit()
    conn.close()
    return True


def normalize_positions(quiz_name: str):
    questions = get_questions(quiz_name)
    conn = db()
    cur = conn.cursor()
    for i, q in enumerate(questions):
        cur.execute("UPDATE questions SET position=? WHERE id=?", (i, q["id"]))
    conn.commit()
    conn.close()


def delete_question_by_index(quiz_name: str, index: int):
    questions = get_questions(quiz_name)
    if not (0 <= index < len(questions)):
        return None
    q = questions[index]
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM questions WHERE id=?", (q["id"],))
    conn.commit()
    conn.close()
    normalize_positions(quiz_name)
    return q


def mark_question_shown(question_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE questions SET shown = shown + 1 WHERE id=?", (question_id,))
    conn.commit()
    conn.close()


def mark_question_stat(question_id: int, correct_answer: bool):
    conn = db()
    cur = conn.cursor()
    if correct_answer:
        cur.execute("UPDATE questions SET correct = correct + 1, shown = shown + 1 WHERE id=?", (question_id,))
    else:
        cur.execute("UPDATE questions SET wrong = wrong + 1, shown = shown + 1 WHERE id=?", (question_id,))
    conn.commit()
    conn.close()


def save_result(user_id: int, user_name: str, quiz_name: str, score: int, total: int):
    conn = db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO results(user_id, user_name, quiz_name, score, total) VALUES (?, ?, ?, ?, ?)",
        (user_id, user_name, quiz_name, score, total),
    )
    conn.commit()
    conn.close()


def get_users_count():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM users")
    c = cur.fetchone()[0]
    conn.close()
    return c


def get_top10():
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT user_name, MAX(score) AS best_score
        FROM results
        GROUP BY user_id
        ORDER BY best_score DESC, user_name ASC
        LIMIT 10
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def count_quiz_stats(quiz_name: str):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM results WHERE quiz_name=?", (quiz_name,))
    plays = cur.fetchone()[0]
    cur.execute("SELECT AVG(score) FROM results WHERE quiz_name=?", (quiz_name,))
    avg = cur.fetchone()[0] or 0
    conn.close()
    return plays, avg


def clear_user_state(uid: int):
    cancel_timer(uid)
    USER_STATE[uid] = {}


# =========================
# KEYBOARDS
# =========================
def main_menu(uid: int):
    rows = [
        ["🎯 Викторины", "🏆 ТОП-10"],
        ["📢 Соцсети"],
    ]
    if is_admin(uid):
        rows.append(["⚙️ Админка"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def back_menu(uid: int):
    rows = [["🎯 Выбрать другую викторину"], ["📢 Соцсети"]]
    if is_admin(uid):
        rows.append(["⚙️ Админка"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_menu_kb():
    return ReplyKeyboardMarkup(
        [
            ["➕ Добавить вопрос", "🧩 Создать викторину"],
            ["📝 Описание викторины", "✏️ Управление вопросами"],
            ["🗑 Удалить вопрос", "❌ Удалить викторину"],
            ["📋 Список викторин", "📊 Статистика"],
            ["👥 Пользователи", "🏆 ТОП-10"],
            ["🔙 Выход"],
        ],
        resize_keyboard=True,
    )


def quiz_select_inline(prefix: str):
    rows = []
    for i, q in enumerate(get_quizzes()):
        rows.append([InlineKeyboardButton(f"{i + 1}. {q['name']}", callback_data=f"{prefix}|{i}")])
    rows.append([InlineKeyboardButton("↩️ В админку", callback_data="adminhome")])
    return InlineKeyboardMarkup(rows)


def question_list_inline(quiz_idx: int):
    quiz_name = get_quiz_name_by_index(quiz_idx)
    if quiz_name is None:
        return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ В админку", callback_data="adminhome")]])
    qs = get_questions(quiz_name)
    rows = []
    for i, q in enumerate(qs):
        title = (q["question"] or "Без названия").replace("\n", " ")
        if len(title) > 42:
            title = title[:42] + "…"
        rows.append([InlineKeyboardButton(f"{i + 1}. {title}", callback_data=f"qsel|{quiz_idx}|{i}")])
    rows.append([InlineKeyboardButton("↩️ К викторинам", callback_data="manageq")])
    rows.append([InlineKeyboardButton("↩️ В админку", callback_data="adminhome")])
    return InlineKeyboardMarkup(rows)


def question_panel_inline(quiz_idx: int, qidx: int):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Вопрос", callback_data=f"qact|{quiz_idx}|{qidx}|question"),
                InlineKeyboardButton("✏️ Варианты", callback_data=f"qact|{quiz_idx}|{qidx}|options"),
            ],
            [
                InlineKeyboardButton("✅ Ответ", callback_data=f"qact|{quiz_idx}|{qidx}|answer"),
                InlineKeyboardButton("📸 Фото", callback_data=f"qact|{quiz_idx}|{qidx}|photo"),
            ],
            [
                InlineKeyboardButton("🔼 Вверх", callback_data=f"qact|{quiz_idx}|{qidx}|up"),
                InlineKeyboardButton("🔽 Вниз", callback_data=f"qact|{quiz_idx}|{qidx}|down"),
            ],
            [
                InlineKeyboardButton("📊 Статистика", callback_data=f"qact|{quiz_idx}|{qidx}|stats"),
                InlineKeyboardButton("🗑 Удалить", callback_data=f"qact|{quiz_idx}|{qidx}|delete"),
            ],
            [InlineKeyboardButton("↩️ К списку", callback_data=f"mq|{quiz_idx}")],
            [InlineKeyboardButton("↩️ В админку", callback_data="adminhome")],
        ]
    )


def social_inline():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Telegram", url=SOCIAL_TG)],
            [InlineKeyboardButton("VK", url=SOCIAL_VK)],
        ]
    )


# =========================
# BASIC SCREENS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    context.user_data["user_id"] = update.effective_user.id
    clear_user_state(update.effective_user.id)
    ADMIN_STATE.setdefault(update.effective_user.id, {})

    await update.message.reply_text(
        "👋 Привет!\n\nВыбери действие:",
        reply_markup=main_menu(update.effective_user.id),
    )


async def show_user_quiz_list(message, context: ContextTypes.DEFAULT_TYPE):
    clear_user_state(message.from_user.id)
    ADMIN_STATE.setdefault(message.from_user.id, {}).clear() if False else None
    quizzes = get_quizzes()
    if not quizzes:
        await message.reply_text("Пока нет викторин.", reply_markup=main_menu(message.from_user.id))
        return
    keyboard = [[q["name"]] for q in quizzes]
    await message.reply_text(
        "🎯 Выбери викторину:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def show_quiz_preview(message, context: ContextTypes.DEFAULT_TYPE, quiz_name: str):
    quiz = get_quiz_by_name(quiz_name)
    if not quiz:
        await message.reply_text("Эта викторина уже недоступна.")
        return
    clear_user_state(message.from_user.id)
    context.user_data["selected_quiz"] = quiz_name

    count = get_question_count(quiz_name)
    desc = quiz["description"] or "Описание не задано"
    text = (
        f"📚 {quiz_name}\n\n"
        f"📝 {desc}\n\n"
        f"Вопросов: {count}"
    )
    if count == 0:
        text += "\n\n⚠️ В этой викторине пока нет вопросов."

    await message.reply_text(
        text,
        reply_markup=ReplyKeyboardMarkup([["▶️ Начать"], ["🔙 Назад"]], resize_keyboard=True),
    )


async def show_socials(message):
    await message.reply_text(
        "📢 Если тебе близко церковное искусство, иконы, фрески и библейские сюжеты — загляни к нам.\n\n"
        "Мы рассказываем о Библии через искусство: через образы, символы и традицию, простым и понятным языком.\n\n"
        "Если тебе интересно не просто смотреть на искусство, а понимать, как оно помогает читать Священное Писание глубже — вот наши страницы:",
        reply_markup=social_inline(),
    )


# =========================
# ADMIN SCREEN
# =========================
async def show_admin_panel(message):
    await message.reply_text(
        "⚙️ Админка\n\n"
        "Сначала выбери действие кнопкой ниже.\n"
        "Потом бот сам подскажет, что делать дальше.",
        reply_markup=admin_menu_kb(),
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    ADMIN_STATE.setdefault(update.effective_user.id, {}).clear()
    await show_admin_panel(update.message)


# =========================
# QUIZ FLOW
# =========================
async def send_question(message, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("user_id") or message.from_user.id
    state = USER_STATE.setdefault(uid, {})
    quiz_name = state.get("quiz")

    if not quiz_name:
        await message.reply_text("Сначала выбери викторину.")
        return

    quiz = get_questions(quiz_name)
    if not quiz:
        await message.reply_text(
            "В этой викторине пока нет вопросов.",
            reply_markup=back_menu(uid),
        )
        clear_user_state(uid)
        return

    idx = state.get("q", 0)

    if idx >= len(quiz):
        await finish_quiz(message, context)
        return

    q = quiz[idx]
    state["answered"] = False
    mark_question_shown(q["id"])

    keyboard = ReplyKeyboardMarkup([[o] for o in q["options"]], resize_keyboard=True)
    question_text = q["question"] or "Без текста вопроса"

    if q.get("photo"):
        if len(question_text) <= 900:
            await message.reply_photo(photo=q["photo"], caption=question_text, reply_markup=keyboard)
        else:
            await message.reply_photo(photo=q["photo"])
            await message.reply_text(question_text, reply_markup=keyboard)
    else:
        await message.reply_text(question_text, reply_markup=keyboard)

    cancel_timer(uid)

    async def timer_snapshot(quiz_snapshot: str, question_index: int):
        await asyncio.sleep(TIME_LIMIT)
        current_state = USER_STATE.get(uid, {})
        if (
            current_state.get("mode") == "play"
            and current_state.get("quiz") == quiz_snapshot
            and current_state.get("q") == question_index
            and not current_state.get("answered")
        ):
            current_state["answered"] = True
            current_state["q"] = question_index + 1
            try:
                await message.reply_text("⏰ Время вышло")
            except:
                pass
            await send_question(message, context)

    ACTIVE_TIMERS[uid] = asyncio.create_task(timer_snapshot(quiz_name, idx))


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = USER_STATE.setdefault(uid, {})

    if state.get("answered"):
        return

    quiz_name = state.get("quiz")
    if not quiz_name:
        return

    quiz = get_questions(quiz_name)
    idx = state.get("q", 0)
    if idx >= len(quiz):
        return

    q = quiz[idx]
    user_answer = update.message.text.strip()
    correct = q["answer"].strip()

    state["answered"] = True
    cancel_timer(uid)

    if normalize(user_answer) == normalize(correct):
        state["score"] = state.get("score", 0) + 1
        mark_question_stat(q["id"], True)
        await update.message.reply_text("✅ Верно")
    else:
        mark_question_stat(q["id"], False)
        await update.message.reply_text(f"❌ Неверно\nОтвет: {correct}")

    state["q"] = idx + 1
    await send_question(update.message, context)


async def finish_quiz(message, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("user_id") or message.from_user.id
    state = USER_STATE.get(uid, {})
    quiz_name = state.get("quiz")
    if not quiz_name:
        return

    score = state.get("score", 0)
    total = get_question_count(quiz_name)

    increment_quiz_completion(quiz_name)
    save_result(uid, message.from_user.full_name, quiz_name, score, total)

    clear_user_state(uid)
    context.user_data.pop("selected_quiz", None)

    await message.reply_text(
        f"🎉 Викторина завершена!\n\n"
        f"📊 Результат: {score}/{total}\n\n"
        f"Если понравился формат, подпишись на наши проекты:\n"
        f"{SOCIAL_TG}\n"
        f"{SOCIAL_VK}",
        reply_markup=back_menu(uid),
    )


# =========================
# STATS / PANELS
# =========================
async def show_quiz_stats_summary(message, quiz_idx: int):
    quiz_name = get_quiz_name_by_index(quiz_idx)
    if quiz_name is None:
        await message.reply_text("Викторина не найдена.")
        return

    quiz = get_quiz_by_name(quiz_name)
    qs = get_questions(quiz_name)
    plays, avg = count_quiz_stats(quiz_name)

    shown = sum(q["shown"] for q in qs)
    correct = sum(q["correct"] for q in qs)
    wrong = sum(q["wrong"] for q in qs)

    text = (
        f"📊 Статистика викторины: {quiz_name}\n\n"
        f"Описание: {quiz['description'] or 'Описание не задано'}\n"
        f"Вопросов: {len(qs)}\n"
        f"Запусков: {quiz['starts']}\n"
        f"Завершений: {quiz['completions']}\n"
        f"Средний результат: {avg:.2f}\n\n"
        f"Показов вопросов: {shown}\n"
        f"Правильных ответов: {correct}\n"
        f"Ошибок: {wrong}\n"
    )
    await send_chunked(message, text)


async def show_questions_stats_summary(message, quiz_idx: int):
    quiz_name = get_quiz_name_by_index(quiz_idx)
    if quiz_name is None:
        await message.reply_text("Викторина не найдена.")
        return

    qs = get_questions(quiz_name)
    if not qs:
        await message.reply_text("В этой викторине пока нет вопросов.")
        return

    text = f"📈 Статистика вопросов: {quiz_name}\n\n"
    for i, q in enumerate(qs):
        total = q["correct"] + q["wrong"]
        accuracy = (q["correct"] / total * 100) if total else 0
        title = (q["question"] or "Без названия").replace("\n", " ")
        if len(title) > 60:
            title = title[:60] + "…"
        text += (
            f"{i + 1}. {title}\n"
            f"   Показов: {q['shown']}\n"
            f"   Правильных: {q['correct']}\n"
            f"   Ошибок: {q['wrong']}\n"
            f"   Точность: {accuracy:.1f}%\n\n"
        )
    await send_chunked(message, text)


async def show_question_panel(message, quiz_idx: int, qidx: int):
    quiz_name = get_quiz_name_by_index(quiz_idx)
    if quiz_name is None:
        await message.reply_text("Викторина не найдена.")
        return

    q = get_question(quiz_name, qidx)
    if q is None:
        await message.reply_text("Вопрос не найден.", reply_markup=admin_menu_kb())
        return

    options_text = "\n".join([f"• {x}" for x in q["options"]]) if q["options"] else "• нет вариантов"
    photo_text = "есть" if q["photo"] else "нет"
    total = q["correct"] + q["wrong"]
    accuracy = (q["correct"] / total * 100) if total else 0

    text = (
        f"✏️ Управление вопросом\n\n"
        f"Викторина: {quiz_name}\n"
        f"Номер: {qidx + 1}/{get_question_count(quiz_name)}\n\n"
        f"Вопрос: {q['question']}\n\n"
        f"Варианты:\n{options_text}\n\n"
        f"Правильный ответ: {q['answer']}\n"
        f"Фото: {photo_text}\n\n"
        f"Статистика:\n"
        f"• Показов: {q['shown']}\n"
        f"• Правильных: {q['correct']}\n"
        f"• Ошибок: {q['wrong']}\n"
        f"• Точность: {accuracy:.1f}%\n"
    )

    if q["photo"]:
        if len(text) <= 900:
            await message.reply_photo(photo=q["photo"], caption=text, reply_markup=question_panel_inline(quiz_idx, qidx))
        else:
            await message.reply_photo(photo=q["photo"])
            await message.reply_text(text, reply_markup=question_panel_inline(quiz_idx, qidx))
    else:
        await message.reply_text(text, reply_markup=question_panel_inline(quiz_idx, qidx))


# =========================
# ADMIN CALLBACKS
# =========================
async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        return

    data = q.data
    uid = q.from_user.id
    admin_state = ADMIN_STATE.setdefault(uid, {})

    if data == "adminhome":
        admin_state.clear()
        await show_admin_panel(q.message)
        return

    if data == "list":
        quizzes = get_quizzes()
        if not quizzes:
            await q.message.reply_text("Пока нет викторин.")
            return
        text = "📋 Викторины:\n\n"
        for i, row in enumerate(quizzes):
            text += (
                f"{i + 1}. {row['name']}\n"
                f"   Описание: {row['description'] or 'Описание не задано'}\n"
                f"   Вопросов: {get_question_count(row['name'])}\n\n"
            )
        await send_chunked(q.message, text)
        return

    if data == "users":
        await q.message.reply_text(
            f"👥 Пользователей, которые запускали бота: {get_users_count()}\n"
            f"📚 Викторин: {len(get_quizzes())}\n"
            f"❓ Всего вопросов: {sum(get_question_count(r['name']) for r in get_quizzes())}"
        )
        return

    if data == "top":
        top = get_top10()
        if not top:
            await q.message.reply_text("Пока нет результатов.")
            return
        text = "🏆 ТОП-10 игроков:\n\n"
        for i, row in enumerate(top, 1):
            text += f"{i}. {row['user_name']} — {row['best_score']}\n"
        await q.message.reply_text(text)
        return

    if data == "newquiz":
        admin_state.clear()
        admin_state["mode"] = "newquiz_name"
        await q.message.reply_text(
            "🧩 Создание викторины\n\n"
            "Шаг 1/2 — напиши название викторины.\n"
            "Например: Библейские образы"
        )
        return

    if data == "add":
        if not get_quizzes():
            await q.message.reply_text("Сначала создай викторину кнопкой «🧩 Создать викторину».")
            return
        admin_state.clear()
        admin_state["mode"] = "add_pick_quiz"
        await q.message.reply_text("➕ В какую викторину добавить вопрос?", reply_markup=quiz_select_inline("addto"))
        return

    if data.startswith("addto|"):
        _, idx = data.split("|")
        quiz_idx = int(idx)
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            await q.message.reply_text("Викторина не найдена.")
            return
        admin_state.clear()
        admin_state["mode"] = "add_question"
        admin_state["add_quiz_idx"] = quiz_idx
        admin_state["add_step"] = 1
        admin_state["new_q"] = {"question": "", "options": [], "answer": "", "photo": None}
        await q.message.reply_text(
            f"➕ Добавление вопроса в викторину: {quiz_name}\n\n"
            "Шаг 1/4 — напиши текст вопроса."
        )
        return

    if data == "editdesc":
        if not get_quizzes():
            await q.message.reply_text("Пока нет викторин.")
            return
        admin_state.clear()
        admin_state["mode"] = "editdesc_pick"
        await q.message.reply_text("📝 Выбери викторину, у которой нужно изменить описание:", reply_markup=quiz_select_inline("desc"))
        return

    if data.startswith("desc|"):
        _, idx = data.split("|")
        quiz_idx = int(idx)
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            await q.message.reply_text("Викторина не найдена.")
            return
        admin_state.clear()
        admin_state["mode"] = "edit_desc"
        admin_state["edit_desc_idx"] = quiz_idx
        await q.message.reply_text(f"✏️ Напиши новое описание для викторины «{quiz_name}».")
        return

    if data == "delquiz":
        if not get_quizzes():
            await q.message.reply_text("Пока нет викторин.")
            return
        admin_state.clear()
        admin_state["mode"] = "delquiz_pick"
        await q.message.reply_text("🗑 Выбери викторину, которую нужно удалить:", reply_markup=quiz_select_inline("delqz"))
        return

    if data.startswith("delqz|"):
        _, idx = data.split("|")
        quiz_idx = int(idx)
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            await q.message.reply_text("Викторина не найдена.")
            return
        await q.message.reply_text(
            f"⚠️ Ты точно хочешь удалить викторину «{quiz_name}»?\n\nЭто удалит ВСЕ вопросы внутри.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("❌ Да, удалить", callback_data=f"delqzconfirm|{quiz_idx}")],
                    [InlineKeyboardButton("↩️ Отмена", callback_data="adminhome")],
                ]
            ),
        )
        return

    if data.startswith("delqzconfirm|"):
        _, idx = data.split("|")
        quiz_idx = int(idx)
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            await q.message.reply_text("Викторина не найдена.")
            return
        delete_quiz(quiz_name)
        admin_state.clear()
        await q.message.reply_text(f"🗑 Викторина «{quiz_name}» удалена.")
        await show_admin_panel(q.message)
        return

    if data == "manageq":
        if not get_quizzes():
            await q.message.reply_text("Пока нет викторин.")
            return
        admin_state.clear()
        admin_state["mode"] = "manage_pick_quiz"
        await q.message.reply_text("✏️ Выбери викторину, чтобы управлять вопросами:", reply_markup=quiz_select_inline("mq"))
        return

    if data.startswith("mq|"):
        _, idx = data.split("|")
        quiz_idx = int(idx)
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            await q.message.reply_text("Викторина не найдена.")
            return
        admin_state.clear()
        admin_state["mode"] = "manage_questions"
        admin_state["manage_quiz_idx"] = quiz_idx
        qs = get_questions(quiz_name)
        if not qs:
            await q.message.reply_text(
                f"В викторине «{quiz_name}» пока нет вопросов.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("↩️ К выбору викторины", callback_data="manageq")],
                        [InlineKeyboardButton("↩️ В админку", callback_data="adminhome")],
                    ]
                ),
            )
            return
        await q.message.reply_text(f"✏️ Викторина «{quiz_name}»\n\nВыбери вопрос:", reply_markup=question_list_inline(quiz_idx))
        return

    if data.startswith("qsel|"):
        _, quiz_idx, qidx = data.split("|")
        await show_question_panel(q.message, int(quiz_idx), int(qidx))
        return

    if data.startswith("qact|"):
        _, quiz_idx, qidx, action = data.split("|")
        quiz_idx = int(quiz_idx)
        qidx = int(qidx)
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            await q.message.reply_text("Викторина не найдена.")
            return

        question = get_question(quiz_name, qidx)
        if question is None:
            await q.message.reply_text("Вопрос не найден.")
            return

        if action == "question":
            admin_state.clear()
            admin_state["mode"] = "edit_q_question"
            admin_state["edit_meta"] = {"quiz_idx": quiz_idx, "qidx": qidx}
            await q.message.reply_text("✏️ Напиши новый текст вопроса:")
            return

        if action == "options":
            admin_state.clear()
            admin_state["mode"] = "edit_q_options"
            admin_state["edit_meta"] = {"quiz_idx": quiz_idx, "qidx": qidx}
            await q.message.reply_text("✏️ Напиши новые варианты через запятую.\nПотом я попрошу правильный ответ.")
            return

        if action == "answer":
            admin_state.clear()
            admin_state["mode"] = "edit_q_answer"
            admin_state["edit_meta"] = {"quiz_idx": quiz_idx, "qidx": qidx}
            await q.message.reply_text("✅ Напиши правильный ответ:")
            return

        if action == "photo":
            admin_state.clear()
            admin_state["mode"] = "replace_photo"
            admin_state["edit_meta"] = {"quiz_idx": quiz_idx, "qidx": qidx}
            await q.message.reply_text("📸 Отправь новое фото.\nИли напиши «Удалить фото», чтобы убрать картинку.")
            return

        if action == "up":
            if qidx == 0:
                await q.message.reply_text("Этот вопрос уже первый.")
                return
            swap_question_positions(quiz_name, qidx, qidx - 1)
            await q.message.reply_text("🔼 Перемещено выше.")
            await show_question_panel(q.message, quiz_idx, qidx - 1)
            return

        if action == "down":
            qs = get_questions(quiz_name)
            if qidx >= len(qs) - 1:
                await q.message.reply_text("Этот вопрос уже последний.")
                return
            swap_question_positions(quiz_name, qidx, qidx + 1)
            await q.message.reply_text("🔽 Перемещено ниже.")
            await show_question_panel(q.message, quiz_idx, qidx + 1)
            return

        if action == "stats":
            total = question["correct"] + question["wrong"]
            accuracy = (question["correct"] / total * 100) if total else 0
            await q.message.reply_text(
                f"📊 Статистика вопроса\n\n"
                f"Викторина: {quiz_name}\n"
                f"Вопрос: {question['question']}\n\n"
                f"Показов: {question['shown']}\n"
                f"Правильных: {question['correct']}\n"
                f"Ошибок: {question['wrong']}\n"
                f"Точность: {accuracy:.1f}%"
            )
            return

        if action == "delete":
            await q.message.reply_text(
                "⚠️ Удалить этот вопрос?",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("❌ Да, удалить", callback_data=f"qdelconfirm|{quiz_idx}|{qidx}")],
                        [InlineKeyboardButton("↩️ Отмена", callback_data=f"qsel|{quiz_idx}|{qidx}")],
                    ]
                ),
            )
            return

    if data.startswith("qdelconfirm|"):
        _, quiz_idx, qidx = data.split("|")
        quiz_idx = int(quiz_idx)
        qidx = int(qidx)
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            await q.message.reply_text("Викторина не найдена.")
            return
        removed = delete_question_by_index(quiz_name, qidx)
        if removed is None:
            await q.message.reply_text("Вопрос не найден.")
            return
        await q.message.reply_text(f"🗑 Удалено: {removed['question']}")
        qs = get_questions(quiz_name)
        if qs:
            new_idx = min(qidx, len(qs) - 1)
            await show_question_panel(q.message, quiz_idx, new_idx)
        else:
            await q.message.reply_text("В этой викторине больше нет вопросов.", reply_markup=question_list_inline(quiz_idx))
        return

    if data == "quizstats":
        if not get_quizzes():
            await q.message.reply_text("Пока нет викторин.")
            return
        await q.message.reply_text("📊 Выбери викторину для статистики:", reply_markup=quiz_select_inline("quizstats"))
        return

    if data.startswith("quizstats|"):
        _, idx = data.split("|")
        await show_quiz_stats_summary(q.message, int(idx))
        return

    if data == "qstats":
        if not get_quizzes():
            await q.message.reply_text("Пока нет викторин.")
            return
        await q.message.reply_text("📈 Выбери викторину для статистики вопросов:", reply_markup=quiz_select_inline("qstats"))
        return

    if data.startswith("qstats|"):
        _, idx = data.split("|")
        await show_questions_stats_summary(q.message, int(idx))
        return

    await q.message.reply_text("Неизвестное действие.")


# =========================
# TEXT HANDLER
# =========================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    register_user(update.effective_user)
    context.user_data["user_id"] = uid

    text = (update.message.text or "").strip()
    user_state = USER_STATE.setdefault(uid, {})
    admin_state = ADMIN_STATE.setdefault(uid, {})
    mode = admin_state.get("mode")

    # common navigation
    if text == "📢 Соцсети":
        await show_socials(update.message)
        return

    if text == "🏆 ТОП-10":
        top = get_top10()
        if not top:
            await update.message.reply_text("🏆 Пока нет результатов.")
            return
        msg = "🏆 ТОП-10 игроков:\n\n"
        for i, row in enumerate(top, 1):
            msg += f"{i}. {row['user_name']} — {row['best_score']}\n"
        await update.message.reply_text(msg)
        return

    if text == "🎯 Викторины":
        await show_user_quiz_list(update.message, context)
        return

    if text == "🎯 Выбрать другую викторину":
        await show_user_quiz_list(update.message, context)
        return

    if text == "🔙 Назад":
        await show_user_quiz_list(update.message, context)
        return

    # admin entry/exit
    if text == "⚙️ Админка" and is_admin(uid):
        admin_state.clear()
        await show_admin_panel(update.message)
        return

    if text == "🔙 Выход" and is_admin(uid):
        admin_state.clear()
        await update.message.reply_text("Главное меню", reply_markup=main_menu(uid))
        return

    # admin flows have priority
    if is_admin(uid) and mode:
        if mode == "newquiz_name":
            quiz_name = text
            if not quiz_name:
                await update.message.reply_text("Название не должно быть пустым. Напиши ещё раз:")
                return
            if get_quiz_by_name(quiz_name):
                await update.message.reply_text("Такая викторина уже есть. Напиши другое название:")
                return
            admin_state["pending_quiz_name"] = quiz_name
            admin_state["mode"] = "newquiz_desc"
            await update.message.reply_text(f"Шаг 2/2 — напиши описание для викторины «{quiz_name}».")
            return

        if mode == "newquiz_desc":
            quiz_name = admin_state.get("pending_quiz_name")
            if not quiz_name:
                admin_state.clear()
                await update.message.reply_text("Ошибка создания викторины.")
                return
            create_quiz(quiz_name, text or "Описание не задано")
            admin_state.clear()
            await update.message.reply_text(f"✅ Викторина «{quiz_name}» создана.")
            await show_admin_panel(update.message)
            return

        if mode == "edit_desc":
            quiz_idx = admin_state.get("edit_desc_idx")
            quiz_name = get_quiz_name_by_index(quiz_idx) if quiz_idx is not None else None
            if quiz_name is None:
                admin_state.clear()
                await update.message.reply_text("Викторина не найдена.")
                return
            update_quiz_description(quiz_name, text or "Описание не задано")
            admin_state.clear()
            await update.message.reply_text("✅ Описание обновлено.")
            await show_admin_panel(update.message)
            return

        if mode == "add_question":
            quiz_idx = admin_state.get("add_quiz_idx")
            quiz_name = get_quiz_name_by_index(quiz_idx) if quiz_idx is not None else None
            if quiz_name is None:
                admin_state.clear()
                await update.message.reply_text("Викторина не найдена.")
                return

            step = admin_state.get("add_step", 1)
            new_q = admin_state.get("new_q", {"question": "", "options": [], "answer": "", "photo": None})

            if step == 1:
                new_q["question"] = text
                admin_state["add_step"] = 2
                admin_state["new_q"] = new_q
                await update.message.reply_text("Шаг 2/4 — напиши варианты через запятую.")
                return

            if step == 2:
                options = [x.strip() for x in text.split(",") if x.strip()]
                if len(options) < 2:
                    await update.message.reply_text("Нужно минимум 2 варианта. Напиши ещё раз через запятую.")
                    return
                new_q["options"] = options
                admin_state["add_step"] = 3
                admin_state["new_q"] = new_q
                await update.message.reply_text("Шаг 3/4 — напиши правильный ответ.")
                return

            if step == 3:
                if find_matching_option(new_q.get("options", []), text) is None:
                    await update.message.reply_text("Правильный ответ должен совпадать с одним из вариантов. Напиши ещё раз:")
                    return
                new_q["answer"] = text
                admin_state["add_step"] = 4
                admin_state["new_q"] = new_q
                await update.message.reply_text("Шаг 4/4 — отправь фото. Или напиши «Пропустить».")
                return

            if step == 4:
                if text.lower() in {"пропустить", "без фото"}:
                    add_question(quiz_name, new_q["question"], new_q["options"], new_q["answer"], None)
                    admin_state.clear()
                    await update.message.reply_text("✅ Вопрос добавлен без фото.")
                    await show_admin_panel(update.message)
                    return
                await update.message.reply_text("Отправь фото или напиши «Пропустить».")
                return

        if mode == "edit_q_question":
            meta = admin_state.get("edit_meta", {})
            quiz_name = get_quiz_name_by_index(meta.get("quiz_idx", -1))
            qidx = meta.get("qidx")
            if quiz_name is None or qidx is None:
                admin_state.clear()
                await update.message.reply_text("Ошибка редактирования.")
                return
            q = get_question(quiz_name, qidx)
            if q is None:
                admin_state.clear()
                await update.message.reply_text("Вопрос не найден.")
                return
            update_question_field(q["id"], "question", text)
            admin_state.clear()
            await update.message.reply_text("✅ Текст вопроса обновлён.")
            await show_question_panel(update.message, meta["quiz_idx"], qidx)
            return

        if mode == "edit_q_options":
            meta = admin_state.get("edit_meta", {})
            quiz_name = get_quiz_name_by_index(meta.get("quiz_idx", -1))
            qidx = meta.get("qidx")
            if quiz_name is None or qidx is None:
                admin_state.clear()
                await update.message.reply_text("Ошибка редактирования.")
                return
            q = get_question(quiz_name, qidx)
            if q is None:
                admin_state.clear()
                await update.message.reply_text("Вопрос не найден.")
                return
            options = [x.strip() for x in text.split(",") if x.strip()]
            if len(options) < 2:
                await update.message.reply_text("Нужно минимум 2 варианта. Напиши ещё раз через запятую.")
                return
            update_question_field(q["id"], "options", json.dumps(options, ensure_ascii=False))
            admin_state["mode"] = "edit_q_answer_after_options"
            admin_state["edit_meta"] = meta
            await update.message.reply_text("Теперь напиши правильный ответ. Он должен совпадать с одним из вариантов.")
            return

        if mode == "edit_q_answer_after_options":
            meta = admin_state.get("edit_meta", {})
            quiz_name = get_quiz_name_by_index(meta.get("quiz_idx", -1))
            qidx = meta.get("qidx")
            if quiz_name is None or qidx is None:
                admin_state.clear()
                await update.message.reply_text("Ошибка редактирования.")
                return
            q = get_question(quiz_name, qidx)
            if q is None:
                admin_state.clear()
                await update.message.reply_text("Вопрос не найден.")
                return
            if find_matching_option(q["options"], text) is None:
                await update.message.reply_text("Ответ должен совпадать с одним из вариантов. Напиши ещё раз:")
                return
            update_question_field(q["id"], "answer", text)
            admin_state.clear()
            await update.message.reply_text("✅ Варианты и правильный ответ обновлены.")
            await show_question_panel(update.message, meta["quiz_idx"], qidx)
            return

        if mode == "edit_q_answer":
            meta = admin_state.get("edit_meta", {})
            quiz_name = get_quiz_name_by_index(meta.get("quiz_idx", -1))
            qidx = meta.get("qidx")
            if quiz_name is None or qidx is None:
                admin_state.clear()
                await update.message.reply_text("Ошибка редактирования.")
                return
            q = get_question(quiz_name, qidx)
            if q is None:
                admin_state.clear()
                await update.message.reply_text("Вопрос не найден.")
                return
            if find_matching_option(q["options"], text) is None:
                await update.message.reply_text("Ответ должен совпадать с одним из вариантов. Напиши ещё раз:")
                return
            update_question_field(q["id"], "answer", text)
            admin_state.clear()
            await update.message.reply_text("✅ Правильный ответ обновлён.")
            await show_question_panel(update.message, meta["quiz_idx"], qidx)
            return

        if mode == "replace_photo":
            meta = admin_state.get("edit_meta", {})
            quiz_name = get_quiz_name_by_index(meta.get("quiz_idx", -1))
            qidx = meta.get("qidx")
            if quiz_name is None or qidx is None:
                admin_state.clear()
                await update.message.reply_text("Ошибка редактирования.")
                return
            q = get_question(quiz_name, qidx)
            if q is None:
                admin_state.clear()
                await update.message.reply_text("Вопрос не найден.")
                return
            if text.lower() in {"удалить фото", "без фото"}:
                update_question_field(q["id"], "photo", None)
                admin_state.clear()
                await update.message.reply_text("✅ Фото удалено.")
                await show_question_panel(update.message, meta["quiz_idx"], qidx)
                return
            await update.message.reply_text("Отправь фото или напиши «Удалить фото» / «Без фото».")
            return

    # if user is currently playing, treat message as answer before anything else
    if user_state.get("mode") == "play" and user_state.get("quiz"):
        await handle_answer(update, context)
        return

    quiz_names = get_quiz_names()

    if text in quiz_names:
        await show_quiz_preview(update.message, context, text)
        return

    if text == "▶️ Начать":
        quiz_name = context.user_data.get("selected_quiz")
        if not quiz_name or not get_quiz_by_name(quiz_name):
            await update.message.reply_text("Сначала выбери викторину.")
            await show_user_quiz_list(update.message, context)
            return
        clear_user_state(uid)
        USER_STATE[uid] = {"quiz": quiz_name, "q": 0, "score": 0, "answered": False, "mode": "play"}
        context.user_data["selected_quiz"] = quiz_name
        increment_quiz_start(quiz_name)
        await send_question(update.message, context)
        return

    await start(update, context)


# =========================
# PHOTO HANDLER
# =========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return

    admin_state = ADMIN_STATE.setdefault(uid, {})
    mode = admin_state.get("mode")

    if mode == "add_question" and admin_state.get("add_step") == 4:
        quiz_idx = admin_state.get("add_quiz_idx")
        quiz_name = get_quiz_name_by_index(quiz_idx)
        if quiz_name is None:
            admin_state.clear()
            await update.message.reply_text("Викторина не найдена.")
            return
        new_q = admin_state.get("new_q", {})
        add_question(quiz_name, new_q["question"], new_q["options"], new_q["answer"], update.message.photo[-1].file_id)
        admin_state.clear()
        await update.message.reply_text("✅ Вопрос с фото добавлен.")
        await show_admin_panel(update.message)
        return

    if mode == "replace_photo":
        meta = admin_state.get("edit_meta", {})
        quiz_name = get_quiz_name_by_index(meta.get("quiz_idx", -1))
        qidx = meta.get("qidx")
        if quiz_name is None or qidx is None:
            admin_state.clear()
            await update.message.reply_text("Ошибка редактирования.")
            return
        q = get_question(quiz_name, qidx)
        if q is None:
            admin_state.clear()
            await update.message.reply_text("Вопрос не найден.")
            return
        update_question_field(q["id"], "photo", update.message.photo[-1].file_id)
        admin_state.clear()
        await update.message.reply_text("✅ Фото заменено.")
        await show_question_panel(update.message, meta["quiz_idx"], qidx)
        return


# =========================
# MAIN
# =========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))

    app.add_handler(CallbackQueryHandler(admin_buttons))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()


if __name__ == "__main__":
    main()
