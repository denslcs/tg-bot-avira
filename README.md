# Tg_bot_AVIRA

MVP Telegram bot on Python (aiogram) with AI (Gemini/OpenAI), dialog memory, credits, and Telegram Mini App.

## Quick start (Windows PowerShell)

Create venv:

```powershell
python -m venv .venv
```

Activate venv:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install deps:

```powershell
python -m pip install -r requirements.txt
```

Create `.env` from example:

```powershell
Copy-Item .env.example .env
```

Run (we will add `src/bot.py` later):

```powershell
python -m src.bot
```
