import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import dis_snek


class BotLogger(logging.getLoggerClass()):
    db_log_level = 25
    important_log_level = 29

    def __init__(self, name, level=logging.NOTSET):
        super().__init__(name, level)
        # TODO: move to config?
        logging.addLevelName(self.db_log_level, "DATABASE")
        logging.addLevelName(self.important_log_level, "IMPORTANT")

    def db(self, msg, *args, **kwargs):
        if self.isEnabledFor(self.db_log_level):
            self._log(self.db_log_level, msg, args, **kwargs)

    def command(self, ctx: dis_snek.InteractionContext, msg, *args, **kwargs):
        self.important(f"{ctx.guild}.{ctx.channel}, by {ctx.author} :: {msg}", *args, **kwargs)

    def important(self, msg, *args, **kwargs):
        if self.isEnabledFor(self.important_log_level):
            self._log(self.important_log_level, msg, args, **kwargs)


def configure_logging(root_logger: BotLogger, logs_dir: Path, log_level: int = logging.INFO):
    logs_dir.mkdir(parents=True, exist_ok=True)

    handlers = [
        TimedRotatingFileHandler(logs_dir / f"bot.log", when="W0", encoding="utf-8"),  # files rotate weekly at mondays
        logging.StreamHandler(),  # stderr
    ]

    formatter = logging.Formatter("[%(asctime)s] [%(levelname)-9.9s]-[%(name)-15.15s]: %(message)s")

    for handler in handlers:
        handler.setFormatter(formatter)
        handler.setLevel(log_level)
        root_logger.addHandler(handler)

    logging.setLoggerClass(BotLogger)

    snek_logger = logging.getLogger(dis_snek.logger_name)
    snek_logger.setLevel(log_level)
    root_logger.setLevel(log_level)
