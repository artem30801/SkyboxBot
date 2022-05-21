import logging
import datetime

from typing import Any

import utils.misc as utils
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
        use_state_management = True

        # Cache config
        use_cache = True
        cache_expiration_time = datetime.timedelta(seconds=10)
        cache_capacity = 20


class Ordered:
    priority: NonNegativeInt


# Generic model operations
async def generic_edit(obj: Document, fields: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    before = obj.dict()

    for key, value in fields.items():
        setattr(obj, key, value)

    if not obj.is_changed:
        return {}

    logger.db(f"Updating {obj}")
    # Without this validator for links is failing...
    # There might be a better workaround for that?
    await obj.save_changes()

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


def validate_emoji(cls, value):
    if value is None:
        return value
    if not utils.is_emoji(value):
        raise ValidationError(f"{value} is not a valid emoji")
    return value

# def validate_color(color: str):
#     """Removes color description from the incorrect client autocomplete logic and checks, if the color is valid"""
#     if not is_hex(color):
#         raise BadBotArgument(f"'{color}' is not a hex color! Put hex color or use auto-complete results")

