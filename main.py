import asyncio
import inspect
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import dis_snek.api.events
from beanie import init_beanie
from dis_snek import AllowedMentions, InteractionContext, Snake, errors, listen
from dis_snek.models import Intents
from motor import motor_asyncio

import utils.log as log_utils
from config import load_settings
from utils import misc as utils

logger: log_utils.BotLogger = logging.getLogger()  # type: ignore


class Bot(Snake):
    def __init__(self, current_dir, config):
        self.current_dir: Path = current_dir

        self.config = config
        # self.config.default_manage_group = db.to_db_name(self.config.default_manage_group)  # todo remove?

        super().__init__(
            intents=Intents.DEFAULT,
            sync_interactions=True,
            # delete_unused_application_cmds=True,
            asyncio_debug=self.config.debug,
            activity="with fractals",  # todo config
            debug_scope=self.config.debug_scope or dis_snek.MISSING,
            default_prefix=["!", dis_snek.MENTION_PREFIX],
        )

        self.db: Optional[motor_asyncio.AsyncIOMotorClient] = None
        self.models = list()

        self.emojis = dict()

    def get_extensions(self):
        current = set(inspect.getmodule(scale).__name__ for scale in self.scales.values())
        search = (self.current_dir / "scales").glob("*.py")
        files = set(path.relative_to(self.current_dir).with_suffix("").as_posix().replace("/", ".") for path in search)

        return current | files

    async def startup(self):
        for extension in self.get_extensions():
            try:
                self.load_extension(extension)
            except Exception as e:
                logger.error(f"Failed to load extension {extension}: {e}")

        if self.config.debug:
            self.grow_scale("dis_snek.ext.debug_scale")

        self.db = motor_asyncio.AsyncIOMotorClient(self.config.database_address)
        await init_beanie(database=self.db.db_name, document_models=self.models)
        await self.astart(self.config.discord_token)

    @listen()
    async def on_ready(self):
        msg = f"Logged in as {self.user}. Current scales: {', '.join(self.get_extensions())}"
        logger.info(msg)
        print(msg)

        logger.info("Pre-loading system custom emojis!")
        for emoji in utils.SystemEmojis:
            home_guild = self.get_guild(self.config.emoji_guild)
            self.emojis[emoji] = await home_guild.fetch_custom_emoji(emoji.value)
        logger.info(f"Pre-loaded {len(self.emojis)} system custom emojis!")

    # @listen()
    # async def on_message_create(self, event: dis_snek.api.events.MessageCreate):
    #     print(event.message.content)

    async def on_command_error(self, ctx: InteractionContext, error: Exception, *args, **kwargs):
        unexpected = True
        if isinstance(error, errors.CommandCheckFailure):
            unexpected = False
            await send_error(
                ctx,
                "Command check failed!\n" "Sorry, but it looks like you don't have permission to use this command!",
            )
        else:
            await send_error(ctx, str(error)[:2000] or "<No exception text available>")

        if unexpected:
            logger.error(f"Exception during command execution: {repr(error)}", exc_info=error)

    def add_model(self, model):
        self.models.append(model)

    def get_emoji(self, emoji: utils.SystemEmojis):
        return self.emojis[emoji]


async def send_error(ctx, msg):
    if not ctx.responded:
        await ctx.send(msg, allowed_mentions=AllowedMentions.none(), ephemeral=True)
    else:
        logger.warning(f"Already responded to message, error message: {msg}")


def main():
    current_dir = Path(__file__).parent

    config = load_settings()
    log_level = logging.DEBUG if config.debug else logging.INFO

    logs_dir = current_dir / "logs"
    log_utils.configure_logging(logger, logs_dir, log_level)

    bot = Bot(current_dir, config)
    asyncio.run(bot.startup())


if __name__ == "__main__":
    main()
