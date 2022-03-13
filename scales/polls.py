import itertools
from datetime import datetime
from typing import Optional

import dis_snek
from dis_snek import InteractionContext, Scale, slash_int_option, slash_str_option, subcommand
from pydantic import BaseModel, Field

from utils.db import Document


class PollOption(BaseModel):
    name: str
    description: str = ""
    emoji: Optional[str] = None
    color: int = dis_snek.ButtonStyles.BLUE

    voted_by: list[int] = Field(default_factory=list)


class Poll(Document):
    name: str
    description: str = ""
    color: Optional[str] = None

    options: list[PollOption] = Field(default_factory=list)
    closes_at: Optional[datetime] = None
    max_choices: int = Field(default=1, ge=1)

    @property
    def voted_by(self) -> set[int]:
        return set(itertools.chain(option.voted_by for option in self.options))

    def choices_by(self, user_id) -> list[PollOption]:
        return [option for option in self.options if user_id in option.voted_by]

    def can_vote(self, user_id) -> bool:
        if len(self.choices_by(user_id)) >= self.max_choices:
            return False
        # todo check time?
        # todo error messages?
        return True


class Polls(Scale):
    @subcommand(base="poll", name="create")
    async def poll_create(
        self,
        ctx: InteractionContext,
        name: slash_str_option(description="Name of the poll", required=True),
        color: slash_str_option(description="Color of the voting embed"),
    ):
        pass


def setup(bot):
    Polls(bot)
    bot.add_model(Poll)
