import logging

from typing import Any

import utils.log as log_utils

from pydantic import Field, NonNegativeInt, validator, ValidationError
from beanie import Document as BeanieDocument
from beanie.odm.queries.find import FindMany

from utils.misc import is_hex, BadBotArgument

logger: log_utils.BotLogger = logging.getLogger(__name__)  # type: ignore


# Base models

class Document(BeanieDocument):
    def __hash__(self):
        return hash(self.id)

    class Settings:
        validate_on_save = True


class Ordered:
    priority: NonNegativeInt


# Generic model operations
async def generic_edit(obj: Document, fields: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    before = obj.dict()

    for key, value in fields.items():
        if value is None:
            continue
        # if isinstance(value, str) and value.lower() == "none":
        #     value =
        setattr(obj, key, value)

    logger.db(f"Updating {obj}")
    await obj.save()

    after = obj.dict()
    diff = {key: (after[key], before[key])
            for key in set(before.keys()) & set(after.keys())
            if after[key] != before[key]
            }

    return diff


async def ensure_priority(query: FindMany, priority: int):
    if await query.find({"priority": priority}).exists():
        await query.find({"priority": {"$gte": priority}}).inc({"priority": 1})


# Converters and validators

def to_db_name(name: str) -> str:
    return name.replace(" ", "_")


def to_display_name(name: str) -> str:
    return name.replace("_", " ")


def validate_name(cls, value):
    return to_db_name(value)


# def validate_color(color: str):
#     """Removes color description from the incorrect client autocomplete logic and checks, if the color is valid"""
#     if not is_hex(color):
#         raise BadBotArgument(f"'{color}' is not a hex color! Put hex color or use auto-complete results")

