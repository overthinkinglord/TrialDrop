import argparse
import asyncio
import logging

from aiogram import Bot
from aiogram.types import BotCommand

from trialtracker.app import build_dispatcher, reminder_worker
from trialtracker.config import load_settings
from trialtracker.database import Database


async def configure_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start and onboarding"),
            BotCommand(command="add", description="How to add a trial"),
            BotCommand(command="list", description="Show active trials"),
            BotCommand(command="stats", description="Show saved money"),
            BotCommand(command="timezone", description="Set timezone"),
            BotCommand(command="help", description="Show help"),
        ]
    )


async def run_bot() -> None:
    settings = load_settings(require_token=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    database = Database(settings.db_path)
    await database.connect()
    await database.initialize()

    bot = Bot(token=settings.bot_token)
    dispatcher = build_dispatcher(database=database, settings=settings)
    worker_task = asyncio.create_task(reminder_worker(bot=bot, database=database, settings=settings))

    await configure_commands(bot)

    try:
        await dispatcher.start_polling(bot)
    finally:
        worker_task.cancel()
        await asyncio.gather(worker_task, return_exceptions=True)
        await database.close()
        await bot.session.close()


async def run_healthcheck() -> None:
    settings = load_settings(require_token=False)
    database = Database(settings.db_path)
    await database.connect()
    await database.initialize()
    await database.close()
    print("ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="TrialDrop Telegram bot")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate config and database connectivity, then exit.",
    )
    args = parser.parse_args()

    if args.check:
        asyncio.run(run_healthcheck())
        return

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
