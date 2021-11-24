import os
import logging
from pathlib import Path
from datetime import datetime

import dis_snek.const
from dis_snek.client import Snake
from dis_snek.models.enums import Intents

from dis_snek.models.listener import listen

logging.basicConfig()
logger = logging.getLogger(dis_snek.const.logger_name)
logger.setLevel(logging.DEBUG)


class Bot(Snake):
    def __init__(self):
        super().__init__(
            intents=Intents.DEFAULT,
            sync_interactions=True,
            delete_unused_application_cmds=True,
            asyncio_debug=True,
            activity="with sneks",
            debug_scope=570257083040137237,
        )

    @listen()
    async def on_ready(self):
        print(f"Logged in as {self.user}")


def main():
    now = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    current_dir = Path(__file__).parent

    logs_dir = current_dir / "logs"
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    handlers = [
        logging.FileHandler(logs_dir / f"{now}.log"),
    ]

    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s] [%(levelname)-9.9s]-[%(name)-15.15s]: %(message)s",
        handlers=handlers,
    )

    bot = Bot()
    # bot.g_id = 701347683591389185

    bot.start((current_dir / "token.txt").read_text().strip())


if __name__ == '__main__':
    main()
