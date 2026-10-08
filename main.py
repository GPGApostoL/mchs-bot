import asyncio
import hashlib
import hmac
import json
import os
import random
import threading
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
import uvicorn

from quiz_data import QUIZ_QUESTIONS

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def _https_origin(value: str) -> str:
    value = value.strip().split(",", maxsplit=1)[0].strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit(("https", parsed.netloc, "", "", ""))


def _mini_app_url() -> str:
    # Prefer the domain actually serving this app over a stale URL override.
    for key in ("REPLIT_DOMAINS", "REPLIT_DEV_DOMAIN"):
        domain = _https_origin(os.environ.get(key, ""))
        if domain:
            return domain
    return _https_origin(os.environ.get("MINI_APP_URL", ""))


MINI_APP_URL = _mini_app_url()
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))
QUESTIONS_PER_TEST = min(50, len(QUIZ_QUESTIONS))
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")

WEB_SESSIONS: dict[str, dict[str, Any]] = {}
WEB_SESSIONS_LOCK = threading.Lock()

app = FastAPI(title="Telegram Qualification Quiz API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRequest(BaseModel):
    init_data: str | None = None


class AnswerRequest(BaseModel):
    session_id: str
    question_index: int
    option_index: int


def _telegram_init_data_user(init_data: str | None) -> dict[str, Any] | None:
    """Validate Telegram Mini App initData when it is available."""
    if not init_data:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None

        secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
        expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            return None

        user_raw = pairs.get("user")
        return json.loads(user_raw) if user_raw else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _public_question(question: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": question["question"],
        "options": question["options"],
        "answer_inferred": question.get("answer_inferred", False),
    }


def _new_web_session(init_data: str | None) -> dict[str, Any]:
    user = _telegram_init_data_user(init_data)
    selected = random.sample(QUIZ_QUESTIONS, QUESTIONS_PER_TEST)
    prepared: list[dict[str, Any]] = []

    for source in selected:
        options = list(source["options"])
        correct_text = source["answer"]
        random.shuffle(options)
        prepared.append(
            {
                "question": source["question"],
                "options": options,
                "correct_index": options.index(correct_text),
                "answer": source["answer"],
                "answer_inferred": source.get("answer_inferred", False),
            }
        )

    session = {
        "id": uuid.uuid4().hex,
        "user": user,
        "questions": prepared,
        "current": 0,
        "score": 0,
        "answers": [],
        "created_at": time.time(),
    }
    return session


@app.get("/")
def web_app() -> FileResponse:
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "questions": len(QUIZ_QUESTIONS), "mini_app_configured": bool(MINI_APP_URL)}


@app.get("/api/quiz/info")
def quiz_info() -> dict[str, Any]:
    return {
        "total_questions_in_source": len(QUIZ_QUESTIONS),
        "questions_per_test": QUESTIONS_PER_TEST,
        "inferred_answers": sum(q.get("answer_inferred", False) for q in QUIZ_QUESTIONS),
    }


@app.post("/api/quiz/start")
def start_quiz(payload: StartRequest) -> dict[str, Any]:
    session = _new_web_session(payload.init_data)
    with WEB_SESSIONS_LOCK:
        WEB_SESSIONS[session["id"]] = session
    first = session["questions"][0]
    return {
        "session_id": session["id"],
        "total": len(session["questions"]),
        "user": session["user"],
        "question_index": 0,
        "question": _public_question(first),
    }


@app.post("/api/quiz/answer")
def answer_quiz(payload: AnswerRequest) -> dict[str, Any]:
    with WEB_SESSIONS_LOCK:
        session = WEB_SESSIONS.get(payload.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Сессия не найдена или уже завершена.")

        current = session["current"]
        if payload.question_index != current:
            raise HTTPException(status_code=409, detail="Вопрос уже обработан или указан неверный номер.")
        if not 0 <= payload.option_index < 4:
            raise HTTPException(status_code=400, detail="Некорректный вариант ответа.")

        question = session["questions"][current]
        correct = payload.option_index == question["correct_index"]
        if correct:
            session["score"] += 1

        session["answers"].append(
            {
                "question_index": current,
                "selected_index": payload.option_index,
                "correct_index": question["correct_index"],
                "correct": correct,
            }
        )
        session["current"] += 1

        next_question = None
        if session["current"] < len(session["questions"]):
            next_question = _public_question(session["questions"][session["current"]])

        finished = next_question is None
        result = {
            "correct": correct,
            "correct_index": question["correct_index"],
            "correct_answer": question["answer"],
            "question_index": current,
            "finished": finished,
            "next_question_index": session["current"] if not finished else None,
            "next_question": next_question,
        }

        if finished:
            total = len(session["questions"])
            result["score"] = session["score"]
            result["total"] = total
            result["percent"] = round(session["score"] / total * 100)

        return result


@app.get("/api/quiz/{session_id}/result")
def quiz_result(session_id: str) -> dict[str, Any]:
    with WEB_SESSIONS_LOCK:
        session = WEB_SESSIONS.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Сессия не найдена.")
        total = len(session["questions"])
        return {
            "finished": session["current"] >= total,
            "score": session["score"],
            "total": total,
            "percent": round(session["score"] / total * 100),
        }


def _main_keyboard() -> InlineKeyboardMarkup:
    if MINI_APP_URL:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("🚀 Открыть тестирование", web_app=WebAppInfo(url=MINI_APP_URL))]]
        )
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("ℹ️ Mini App ещё не настроен", callback_data="no_app")]]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🟢 **КВАЛИФИКАЦИОННОЕ ТЕСТИРОВАНИЕ**\n\n"
        "Интерактивный тест в Telegram Mini App.\n\n"
        f"• В базе: **{len(QUIZ_QUESTIONS)}** вопросов\n"
        f"• За один тест: **{QUESTIONS_PER_TEST}** случайных вопросов\n"
        "• Ответ можно выбрать нажатием на всю карточку\n\n"
        "Нажмите кнопку ниже, чтобы открыть приложение."
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=_main_keyboard())


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "no_app":
        await query.message.reply_text(
            "Mini App пока не подключён. Задайте переменную MINI_APP_URL после публикации фронтенда."
        )


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


async def run_services() -> None:
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured. Add it in Replit Secrets."
        )

    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CallbackQueryHandler(callback))
    if bot.updater is None:
        raise RuntimeError("Telegram polling is unavailable in this application.")

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=API_HOST,
            port=API_PORT,
            log_level="info",
        )
    )
    api_task = asyncio.create_task(server.serve(), name="quiz-api")
    initialized = False
    started = False
    polling = False

    try:
        while not server.started:
            if api_task.done():
                await api_task
                raise RuntimeError("FastAPI stopped before it finished starting.")
            await asyncio.sleep(0.05)

        await bot.initialize()
        initialized = True
        await bot.start()
        started = True
        await bot.updater.start_polling()
        polling = True

        print(f"FastAPI and Telegram bot are running on 0.0.0.0:{API_PORT}.")
        await api_task
    finally:
        server.should_exit = True
        if not api_task.done():
            await api_task
        if polling and bot.updater.running:
            await bot.updater.stop()
        if started:
            await bot.stop()
        if initialized:
            await bot.shutdown()


def main() -> None:
    asyncio.run(run_services())


if __name__ == "__main__":
    main()
