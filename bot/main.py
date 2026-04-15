# ============================================================
# bot/main.py — Point d'entrée du bot
# ============================================================

import logging
from telegram.ext import ApplicationBuilder, CommandHandler

from config import TELEGRAM_BOT_TOKEN
from bot.handlers import cmd_start, cmd_help, cmd_analyze

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN manquant. "
            "Vérifier le fichier .env ou les variables Railway."
        )

    logger.info("Démarrage du bot TP/SL Analyzer v3...")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("analyze", cmd_analyze))

    logger.info("Bot démarré — en attente de commandes Telegram.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
