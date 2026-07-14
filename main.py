import os
import threading
import subprocess
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from gigachat import GigaChat
from docx import Document
from dotenv import load_dotenv

load_dotenv()

TG_TOKEN = os.getenv("TG_TOKEN")
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")

SUMMARY_PROMPT = """
Сделай краткое изложение подкаста строго по следующему шаблону:

**Краткое изложение:**
(5-8 предложения)

**Главные мысли:**
- 
- 

**Полезные советы, которые могут улучшить жизнь:**
- 
- 

**Техники и упражнения:**
- 
- 

**Рекомендации (книги, фильмы, ресурсы):**
(если есть)

Оригинальная расшифровка:
{transcript}
"""


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


def get_transcript(video_id):
    result = subprocess.run(
        ["yt-dlp", "--write-auto-sub", "--sub-lang", "ru", "--skip-download",
         "--sub-format", "json3", "-o", f"/tmp/{video_id}", f"https://www.youtube.com/watch?v={video_id}"],
        capture_output=True, text=True
    )
    
    import glob
    files = glob.glob(f"/tmp/{video_id}*.json3")
    if not files:
        files = glob.glob(f"/tmp/{video_id}*.vtt")
        if not files:
            raise Exception("Субтитры не найдены")
        with open(files[0], "r", encoding="utf-8") as f:
            content = f.read()
        import re
        text = re.sub(r'<[^>]+>', '', content)
        text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}.*', '', text)
        lines = [l.strip() for l in text.split('\n') if l.strip() and not l.startswith('WEBVTT')]
        return ' '.join(dict.fromkeys(lines))
    
    with open(files[0], "r", encoding="utf-8") as f:
        data = json.load(f)
    
    texts = []
    for event in data.get("events", []):
        for seg in event.get("segs", []):
            t = seg.get("utf8", "").strip()
            if t and t != "\n":
                texts.append(t)
    return " ".join(texts)


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Пришли ссылку на YouTube видео")
        return

    status_msg = await update.message.reply_text("⏳ Извлекаю расшифровку...")

    try:
        if "v=" in url:
            video_id = url.split("v=")[-1].split("&")[0]
        else:
            video_id = url.split("/")[-1].split("?")[0]

        transcript_text = get_transcript(video_id)

        await status_msg.edit_text("🤖 Делаю саммари через ГигаЧат...")

        with GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            verify_ssl_certs=False
        ) as client:
            response = client.chat(
                SUMMARY_PROMPT.format(transcript=transcript_text[:28000])
            )
            summary = response.choices[0].message.content

        await status_msg.edit_text("📄 Создаю Word-файл...")

        doc = Document()
        doc.add_heading('Саммари подкаста', level=0)

        for line in summary.split('\n'):
            line = line.strip()
            if line.startswith('**') and line.endswith('**'):
                doc.add_heading(line.replace('**', '').strip(), level=1)
            elif line.startswith('-'):
                doc.add_paragraph(line, style='List Bullet')
            else:
                if line:
                    doc.add_paragraph(line)

        filename = f"/tmp/summary_{video_id}.docx"
        doc.save(filename)

        await update.message.reply_document(
            document=open(filename, 'rb'),
            caption="✅ Саммари готово!"
        )

        if os.path.exists(filename):
            os.remove(filename)

    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)}")


def main():
    print("Бот запускается...")
    threading.Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(TG_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.run_polling()


if __name__ == "__main__":
    main()
