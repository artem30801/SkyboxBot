import re
from typing import Optional, Union
from datetime import datetime

import emoji
import dis_snek
from dis_snek import Embed, Snake, AutocompleteContext
from dis_snek import FlatUIColors as FlatColors
from dis_snek.models import Color
from emoji import UNICODE_EMOJI_ENGLISH

from utils.color import find_color_name, color_names, colors, rgb2hex, hex2rgb
from utils.fuzz import fuzzy_autocomplete


class SkyBotException(Exception):
    pass


class BadBotArgument(SkyBotException):
    pass


def get_default_embed(guild: dis_snek.Guild, title: Optional[str], status: ResponseStatusColors) -> Embed:
    embed = Embed(color=status.value, title=title)  # noqa
    embed.set_author(name=guild.name, icon_url=guild.icon.url)
    return embed


def format_lines(d: dict, delimiter="|"):
    max_len = max(map(len, d.keys()))
    lines = [f"{name:<{max_len}}{delimiter} {value}" for name, value in d.items()]
    return lines


def member_mention(ctx: dis_snek.InteractionContext, member: dis_snek.Member) -> str:
    return member.mention if member != ctx.author else "You"


def get_developer_ping():
    # TODO: DO
    return "developers"


def can_manage_role(member: dis_snek.Member, role: dis_snek.Role) -> bool:
    """Checks, if member have permissions to manage this role"""
    if not member.guild_permissions().MANAGE_ROLES:
        return False
    return member.top_role > role


def is_emoji(emoji_string: str) -> bool:
    """Checks, if passed string is emoji (discord or UTF one)"""
    if emoji.is_emoji(emoji_string):
        return True
    # Emoji names must be at least 2 characters long and can only contain alphanumeric characters and underscores
    return bool(re.fullmatch(r"<:\w{2,}:[0-9]+>", emoji_string))


def convert_to_db_name(name: str) -> str:
    return name.replace(' ', '_')


def convert_to_name(db_name: str) -> str:
    return db_name.replace('_', ' ')


def is_hex(hex_string: str) -> bool:
    """Checks, if passed string is a valid hex string"""
    return bool(re.fullmatch(r"#?([a-fA-F0-9]{3})|#?([a-fA-F0-9]{6})", hex_string))


async def send_with_embed(
        ctx: dis_snek.InteractionContext,
        embed_text: Optional[str] = None,
        embed_title: Optional[str] = None,
        status_color: ResponseStatusColors = ResponseStatusColors.SUCCESS,
        **kwargs
        ) -> dis_snek.Message:
    """Sends a simple message with text as embed"""
    embeds = [dis_snek.Embed(title=embed_title, description=embed_text, color=status_color.value)]

    if kw_embed := kwargs.pop("embed", None):
        embeds.append(kw_embed)
    if kw_embeds := kwargs.pop("embeds", None):
        embeds.extend(kw_embeds)

    return await ctx.send(embeds=embeds, **kwargs)


async def color_autocomplete(ctx: AutocompleteContext, color: str):
    try:
        if not is_hex(color):
            raise ValueError

        # color is hex
        color_name = find_color_name(color)
        hex_color = rgb2hex(*colors[color_name])
        color = rgb2hex(*hex2rgb(color))  # to normalize hex color string
        # color name might match hex inexactly, so we provide exact and inexact matches
        results = {
            color: color,
            f"{color_name} | {hex_color}": hex_color,
        }
        results = [dict(name=name, value=value) for name, value in results.items()]
    except ValueError:
        # color is color name
        results = fuzzy_autocomplete(color, color_names)
        results = [dict(name=f"{name} | {hex_color}", value=hex_color) for name, _, hex_color in results]

    await ctx.send(results)


def validate_color(color: str):
    """Removes color description from the incorrect client autocomplete logic and checks, if the color is valid"""
    if not is_hex(color):
        raise BadBotArgument(f"'{color}' is not a hex color! Put hex color or use auto-complete results")