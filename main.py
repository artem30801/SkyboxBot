import asyncio
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Optional
import inspect

from motor import motor_asyncio

from dis_snek import AllowedMentions, logger_name, listen, errors
from dis_snek import Snake, InteractionContext
from dis_snek.models import Intents

from beanie import init_beanie

from config import load_settings
from utils import misc as utils

logger = logging.getLogger()


class Bot(Snake):
    def __init__(self, current_dir, config, initial_scales=None):
        self.current_dir: Path = current_dir

        self.config = config
        self.config.default_manage_group = utils.convert_to_db_name(self.config.default_manage_group)

        super().__init__(
            intents=Intents.DEFAULT,
            sync_interactions=True,
            # delete_unused_application_cmds=True,
            asyncio_debug=self.config.debug,
            activity="with sneks",
            debug_scope=self.config.debug_scope,
        )

        self.db: Optional[motor_asyncio.AsyncIOMotorClient] = None
        self.models = list()

    def get_extensions(self):
        current = set(inspect.getmodule(scale).__name__ for scale in self.scales.values())
        search = (self.current_dir / "scales").glob("*.py")
        files = set(path.relative_to(self.current_dir).with_suffix("").as_posix().replace("/", ".") for path in search)

        return current | files

    def startup(self, bot_token, db_token):
        for extension in self.get_extensions():
            try:
                self.load_extension(extension)
            except Exception as e:
                logger.error(f"Failed to load extension {extension}: {e}")

        if self.config.debug:
            self.grow_scale("dis_snek.ext.debug_scale")

        self.db = motor_asyncio.AsyncIOMotorClient(db_token)
        self.loop.run_until_complete(init_beanie(database=self.db.db_name, document_models=self.models))
        try:
            self.loop.run_until_complete(self.login(bot_token))
        except KeyboardInterrupt:
            self.loop.run_until_complete(self.stop())

    @listen()
    async def on_ready(self):
        msg = f"Logged in as {self.user}. Current scales: {', '.join(self.get_extensions())}"
        logger.info(msg)
        print(msg)

    async def on_command_error(self, ctx: InteractionContext, error: Exception, *args, **kwargs):
        unexpected = True
        if isinstance(error, errors.CommandCheckFailure):
            unexpected = False
            await send_error(ctx, "Command check failed!\n"
                                  "Sorry, but it looks like you don't have permission to use this command!")
        else:
            await send_error(ctx, str(error)[:2000] or "<No exception text available>")

        if unexpected:
            logger.error(f"Exception during command execution: {repr(error)}", exc_info=error)

    def add_model(self, model):
        self.models.append(model)


async def send_error(ctx, msg):
    if not ctx.responded:
        await ctx.send(msg, allowed_mentions=AllowedMentions.none(), ephemeral=True)
    else:
        logger.warning(f"Already responded to message, error message: {msg}")


def main():
    config = load_settings()

    current_dir = Path(__file__).parent

    logs_dir = current_dir / "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    handlers = [
        TimedRotatingFileHandler(logs_dir / f"bot.log", when="W0", encoding="utf-8"),  # files rotate weekly at mondays
        logging.StreamHandler(),
    ]

    log_level = logging.DEBUG if config.debug else logging.INFO

    formatter = logging.Formatter("[%(asctime)s] [%(levelname)-9.9s]-[%(name)-15.15s]: %(message)s")

    logging.setLoggerClass(utils.BotLogger)
    snek_logger = logging.getLogger(logger_name)
    snek_logger.setLevel(log_level)

    logger.setLevel(log_level)

    for handler in handlers:
        handler.setFormatter(formatter)
        handler.setLevel(log_level)

        logger.addHandler(handler)

    bot = Bot(current_dir, config)

    bot.startup(config.discord_token, config.database_address)


if __name__ == "__main__":
    main()
