import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ========== CONFIG ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # optional

# Free / cheap models (you can change these)
PRIMARY_MODEL = "meta-llama/llama-3.2-3b-instruct:free"   # OpenRouter free
FALLBACK_MODEL = "google/gemini-flash-1.5"               # if you add Gemini support later

SYSTEM_PROMPT = """You are my personal AI friend. Your name is Aria.

Personality:
- Warm, calm, slightly witty, and genuinely caring — like a close friend who is always there.
- Never overly formal or robotic.
- You can be playful but stay natural.

Language rules:
- Default language is English.
- Switch to Tamil only when I write in Tamil, ask you to speak Tamil, or when it clearly fits the moment.
- Keep replies clear and natural.

Behavior:
- Be a good listener. When I just want to talk, listen and respond supportively.
- Keep most replies short and conversational unless I ask for more.
- Remember important things I tell you about my life, mood, goals, and preferences.
- You may send occasional friendly check-ins (maximum 1–2 times per week). Keep them light.
- Always be ready when I message you."""

# Simple in-memory conversation (for start)
conversation_history = []

# OpenRouter client
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hey, I'm Aria. I'm here whenever you want to talk.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    # Add user message to history
    conversation_history.append({"role": "user", "content": user_message})

    # Keep history short to save credits
    if len(conversation_history) > 12:
        conversation_history.pop(0)
        conversation_history.pop(0)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history

    try:
        response = client.chat.completions.create(
            model=PRIMARY_MODEL,
            messages=messages,
            max_tokens=400,
            temperature=0.7,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error: {e}")
        reply = "Sorry, something went wrong. Try again in a moment."

    conversation_history.append({"role": "assistant", "content": reply})
    await update.message.reply_text(reply)


def main():
    if not TELEGRAM_TOKEN or not OPENROUTER_API_KEY:
        print("Missing TELEGRAM_TOKEN or OPENROUTER_API_KEY")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()