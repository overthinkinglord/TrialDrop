# TrialDrop

Telegram bot for tracking free trials and avoiding unwanted charges.

You send a message like:

`ChatGPT 14 дней $20`

The bot stores the trial, sends a reminder before the expected charge, and counts confirmed saved money after you mark the trial as canceled.

## Stack

- Python 3.9+
- `aiogram` for Telegram bot logic
- `aiosqlite` for durable local storage
- long polling for simple VPS deployment

## Why polling for VPS

For a single bot on a VPS, polling is the simplest production setup:

- no reverse proxy needed for MVP
- no HTTPS webhook setup required
- easy restart via `systemd`
- reminder worker runs in the same process

If the bot grows later, webhook mode can be added without changing the product logic.

## Local Setup

1. Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create your env file:

```bash
cp .env.example .env
```

4. Fill in `BOT_TOKEN` inside `.env`

5. Run a health check:

```bash
python3 main.py --check
```

6. Start the bot:

```bash
python3 main.py
```

## Environment Variables

- `BOT_TOKEN` — Telegram bot token
- `DB_PATH` — SQLite file path, default `data/trialdrop.db`
- `REMINDER_POLL_SECONDS` — how often the worker checks due jobs
- `REMINDER_BATCH_SIZE` — max due reminders per tick
- `LOG_LEVEL` — default `INFO`

## Commands

- `/start`
- `/add`
- `/list`
- `/stats`
- `/timezone`
- `/help`

## Project Layout

- [main.py](/Users/admin/Dev/active/TrialDrop/main.py)
- [trialtracker/app.py](/Users/admin/Dev/active/TrialDrop/trialtracker/app.py)
- [trialtracker/database.py](/Users/admin/Dev/active/TrialDrop/trialtracker/database.py)
- [trialtracker/parser.py](/Users/admin/Dev/active/TrialDrop/trialtracker/parser.py)
- [TRIALDROP_MVP.md](/Users/admin/Dev/active/TrialDrop/TRIALDROP_MVP.md)

## Deployment on VPS

Recommended flow:

1. Clone the repo on the VPS
2. Create `.env` on the server
3. Create a virtual environment
4. Install requirements
5. Run via `systemd`

Example:

```bash
cd /root
git clone https://github.com/your-user/TrialDrop.git
cd TrialDrop
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
python3 main.py --check
```

Then create the service from [systemd/trialdrop.service.example](/Users/admin/Dev/active/TrialDrop/systemd/trialdrop.service.example).

## Update Flow on VPS

```bash
cd /root/TrialDrop
git pull origin main
.venv/bin/pip install -r requirements.txt
systemctl restart trialdrop.service
systemctl status trialdrop.service
```

## Notes

- `.env` should never be committed
- SQLite is enough for MVP and one VPS instance
- reminders survive restarts because jobs and trial state are stored in SQLite
