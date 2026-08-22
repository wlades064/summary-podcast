import asyncio
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

import imageio_ffmpeg
from docx import Document
from dotenv import load_dotenv
from google import genai
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

TG_TOKEN = os.getenv("TG_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
PROCESSING_LOCK = asyncio.Lock()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
LOGGER = logging.getLogger(__name__)

SUMMARY_PROMPT = """
Проанализируй это русскоязычное видео или аудио целиком и подготовь саммари
на русском языке. Не выдумывай факты, рекомендации или названия, которых нет
в материале. Если разделу нечего добавить, напиши «Не упоминалось».

Используй строго эту структуру Markdown:

# Краткое изложение
5–8 содержательных предложений.

# Главные мысли
- конкретные тезисы

# Полезные советы, которые могут улучшить жизнь
- практические советы из материала

# Техники и упражнения
- описанные техники и упражнения

# Рекомендации
- упомянутые книги, фильмы, авторы и ресурсы

# Ключевые фрагменты
- 5–10 ключевых моментов с примерными временными метками вида [ММ:СС]

Сохраняй важные оговорки автора и не своди сложные тезисы к лозунгам.
""".strip()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


def normalize_youtube_url(raw_url: str) -> tuple[str, str]:
    """Return a canonical YouTube URL and its 11-character video id."""
    candidate = raw_url.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    video_id = ""

    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
                video_id = parts[1]
    else:
        raise ValueError("Пришли ссылку с youtube.com или youtu.be")

    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Не удалось определить ID видео в ссылке")

    return f"https://www.youtube.com/watch?v={video_id}", video_id


def create_gemini_client():
    if not GEMINI_API_KEY:
        raise RuntimeError("На сервере не задан GEMINI_API_KEY")
    return genai.Client(api_key=GEMINI_API_KEY)


def request_summary(client, media_input: dict) -> str:
    """Call Gemini with small retries for transient free-tier failures."""
    last_error = None
    for attempt, delay in enumerate((0, 15, 45), start=1):
        if delay:
            time.sleep(delay)
        try:
            response = client.interactions.create(
                model=GEMINI_MODEL,
                input=[media_input, {"type": "text", "text": SUMMARY_PROMPT}],
                timeout=900,
            )
            summary = (response.output_text or "").strip()
            if not summary:
                raise RuntimeError("Gemini вернул пустой ответ")
            return summary
        except Exception as error:
            last_error = error
            LOGGER.warning("Gemini attempt %s failed: %s", attempt, error)
    raise RuntimeError(f"Gemini не смог обработать материал: {last_error}")


def summarize_youtube_url(client, url: str) -> str:
    return request_summary(
        client,
        {"type": "video", "uri": url, "mime_type": "video/mp4"},
    )


def download_audio(url: str, destination: Path) -> Path:
    """Download and convert one video's audio to an API-supported MP3 file."""
    output_template = str(destination / "audio.%(ext)s")
    ffmpeg_dir = str(Path(imageio_ffmpeg.get_ffmpeg_exe()).parent)
    command = [
        "yt-dlp",
        "--no-playlist",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--socket-timeout",
        "30",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "--ffmpeg-location",
        ffmpeg_dir,
        "--output",
        output_template,
        url,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    audio_path = destination / "audio.mp3"
    if result.returncode != 0 or not audio_path.is_file():
        details = (result.stderr or result.stdout)[-800:].strip()
        raise RuntimeError(f"Не удалось скачать аудио с YouTube: {details}")
    return audio_path


def summarize_audio_fallback(client, url: str, destination: Path) -> str:
    audio_path = download_audio(url, destination)
    uploaded_file = None
    try:
        uploaded_file = client.files.upload(file=str(audio_path))
        return request_summary(
            client,
            {
                "type": "audio",
                "uri": uploaded_file.uri,
                "mime_type": uploaded_file.mime_type or "audio/mp3",
            },
        )
    finally:
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception as error:
                LOGGER.warning("Could not delete temporary Gemini file: %s", error)


def generate_summary(url: str, destination: Path) -> tuple[str, bool]:
    """Prefer direct YouTube analysis; fall back to downloaded audio."""
    client = create_gemini_client()
    try:
        return summarize_youtube_url(client, url), False
    except Exception as direct_error:
        LOGGER.warning("Direct YouTube analysis failed, using audio: %s", direct_error)
        return summarize_audio_fallback(client, url, destination), True


def add_markdown_to_document(document: Document, markdown: str):
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            document.add_heading(line.lstrip("#").strip(" *"), level=1)
        elif line.startswith("**") and line.endswith("**"):
            document.add_heading(line.strip("* "), level=1)
        elif re.match(r"^[-*•]\s+", line):
            document.add_paragraph(re.sub(r"^[-*•]\s+", "", line), style="List Bullet")
        else:
            document.add_paragraph(line)


def create_document(summary: str, source_url: str, filename: Path):
    document = Document()
    document.add_heading("Саммари подкаста", level=0)
    document.add_paragraph(f"Источник: {source_url}")
    document.add_paragraph(f"Создано: {datetime.now().astimezone():%d.%m.%Y %H:%M}")
    add_markdown_to_document(document, summary)
    document.save(filename)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or not update.message.text:
        return

    try:
        url, video_id = normalize_youtube_url(update.message.text)
    except ValueError as error:
        await update.message.reply_text(f"❌ {error}")
        return

    status = await update.message.reply_text("⏳ Видео принято, ожидаю обработку…")
    async with PROCESSING_LOCK:
        await status.edit_text("🤖 Анализирую видео целиком через Gemini…")
        try:
            with tempfile.TemporaryDirectory(prefix=f"summary-{video_id}-") as temp_dir:
                temp_path = Path(temp_dir)
                summary, used_audio = await asyncio.to_thread(
                    generate_summary, url, temp_path
                )
                if used_audio:
                    await status.edit_text("📄 Аудио распознано, создаю Word-файл…")
                else:
                    await status.edit_text("📄 Видео проанализировано, создаю Word-файл…")

                filename = temp_path / f"summary_{video_id}.docx"
                await asyncio.to_thread(create_document, summary, url, filename)
                with filename.open("rb") as document:
                    await update.message.reply_document(
                        document=document,
                        filename=filename.name,
                        caption="✅ Саммари готово!",
                    )
            await status.delete()
        except Exception as error:
            LOGGER.exception("Failed to process %s", url)
            message = str(error)
            if len(message) > 900:
                message = message[:900] + "…"
            await status.edit_text(f"❌ Не удалось обработать видео: {message}")


def validate_configuration():
    missing = [
        name
        for name, value in (("TG_TOKEN", TG_TOKEN), ("GEMINI_API_KEY", GEMINI_API_KEY))
        if not value
    ]
    if missing:
        raise RuntimeError(f"Не заданы обязательные переменные: {', '.join(missing)}")


def main():
    validate_configuration()
    LOGGER.info("Starting bot with model %s", GEMINI_MODEL)
    Thread(target=run_health_server, daemon=True).start()
    application = Application.builder().token(TG_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    application.run_polling()


if __name__ == "__main__":
    main()
