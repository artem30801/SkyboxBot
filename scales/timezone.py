import dis_snek
from dis_snek import Scale, InteractionContext, AutocompleteContext, subcommand, slash_str_option, slash_int_option

from utils.fuzz import fuzzy_autocomplete, fuzzy_find

from utils.db import Document
from pydantic import BaseModel, Field

from collections import defaultdict
from typing import Optional
from datetime import datetime

import dateparser
import pytz
from dateutil import tz


class UserTimezone(Document):
    user_id: int
    timezone: str


class Timezones(Scale):
    def __init__(self, client):
        self.timezones = []

        self.abbreviations: defaultdict[str, list] = defaultdict(list)
        self.offsets: defaultdict[str, list] = defaultdict(list)

        for name in pytz.common_timezones:
            timezone = pytz.timezone(name)
            now = datetime.now(timezone)
            offset = self.format_offset(now)
            abbreviation = now.strftime('%Z')

            self.offsets[offset].append(name)
            self.abbreviations[abbreviation].append(name)

        print(self.abbreviations)
        print(self.offsets)

    @subcommand(base="timezone", name="set")
    async def timezone_set(self, ctx: InteractionContext,
                           timezone: slash_str_option("Name or offset of the timezone you are in", required=True, autocomplete=True),
                           ):
        pass

    @timezone_set.autocomplete("timezone")
    async def _timezone_set_tz(self, ctx: AutocompleteContext, timezone, **kwargs):
        results = []
        # abbreviations = fuzzy_autocomplete(timezone, list(self.abbreviations.keys()))
        # for abbreviation, score, _ in abbreviations:
        #     entries = [(name, score) for name in self.abbreviations[abbreviation]]
        #     results.extend(entries)

        # Search by abbreviations and append all timezones with matching abbreviations to the results
        results.extend(self.expand_results(timezone, self.abbreviations))

        # Search by offsets and append all timezones with matching offsets to the results
        results.extend(self.expand_results(timezone, self.offsets, additional_score=30))

        results.extend(fuzzy_autocomplete(timezone, pytz.common_timezones))

        # Sort by score, the highest score LAST
        results.sort(key=lambda item: item[1])
        # Convert to dict and then back to list to make sure that there are no duplicates in names
        # This way, results with the highest score override results with the lowest score
        results = {name: score for name, score, *_ in results}
        results = [(name, score) for name, score in results.items()]
        # Sort by score, the highest score FIRST (bc dict conversion scrambles order)
        results.sort(key=lambda item: item[1], reverse=True)
        # Leave 25 best results
        results = results[:25]
        # Format output
        results = [self.format_timezone(name) + f" | {score}" for name, score in results]
        await ctx.send(results)

    @staticmethod
    def expand_results(query: str, data_dict: dict[str, list[str]], additional_score: int = 0) -> list[tuple[str, int]]:
        results = []
        fuzzy_results = fuzzy_autocomplete(query, list(data_dict.keys()))
        for abbreviation, score, _ in fuzzy_results:
            entries = [(name, score+additional_score) for name in data_dict[abbreviation]]
            results.extend(entries)

        return results

    @classmethod
    def format_timezone(cls, name):
        timezone = pytz.timezone(name)
        now = datetime.now(timezone)
        offset = cls.format_offset(now)
        abbreviation = now.strftime('%Z')
        return f"{offset} | {abbreviation} | {name}"

    @staticmethod
    def format_offset(now):
        offset = now.strftime('%z')
        return f"{offset[:3]}:{offset[3:]}"

# a = tz.gettz('Pacific/Kiritimati')
# # settings={'RETURN_AS_TIMEZONE_AWARE': True}
#
# d = dateparser.parse("Pacific/Kiritimati now")
# print(d)
# print(d.tzname())


def setup(bot):
    Timezones(bot)
    bot.add_model(UserTimezone)
