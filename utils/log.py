import logging

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
