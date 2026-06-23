import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from youtube_transcript_api import YouTubeTranscriptApi
from gigachat import GigaChat
from docx import Document
from dotenv import load_dotenv

load_dotenv()

bot = Bot(token=os.getenv("TG_TOKEN"))
dp = Dispatcher()

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

@dp.message()
async def handle_link(message: types.Message):
    url = message.text.strip()
    if "youtube.com" not in url and "youtu.be" not in url:
        await message.answer("Пришли ссылку на YouTube")
        return

    status = await message.answer("Извлекаю расшифровку...")

    try:
        # Получаем transcript
        video_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1].split("?")[0]
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id, languages=['ru', 'en', 'uk'])
        transcript_text = " ".join([item['text'] for item in transcript_list])

        await bot.edit_message_text("Делаю саммари через ГигаЧат...", status.chat.id, status.message_id)

        # ГигаЧат
        with GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope="GIGACHAT_API_PERS",
            model="GigaChat",           # или GigaChat-2-Pro
            verify_ssl_certs=False
        ) as client:
            response = client.chat.create(
                messages=[
                    {"role": "system", "content": "Ты — профессиональный аналитик подкастов."},
                    {"role": "user", "content": SUMMARY_PROMPT.format(transcript=transcript_text[:25000])}
                ],
                temperature=0.6,
                max_tokens=4000
            )
            summary = response.choices[0].message.content

        await bot.edit_message_text("Создаю Word-файл...", status.chat.id, status.message_id)

        # Создание .docx
        doc = Document()
        doc.add_heading('Саммари подкаста', 0)
        
        for line in summary.split('\n'):
            line = line.strip()
            if line.startswith('**') and line.endswith('**'):
                doc.add_heading(line.replace('**', ''), level=1)
            elif line.startswith('- '):
                doc.add_paragraph(line, style='List Bullet')
            else:
                doc.add_paragraph(line)

        filename = f"summary_{video_id}.docx"
        doc.save(filename)

        await message.answer_document(
            types.FSInputFile(filename),
            caption="Готово ✅"
        )

        os.remove(filename)

    except Exception as e:
        await bot.edit_message_text(f"Ошибка: {str(e)}", status.chat.id, status.message_id)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
