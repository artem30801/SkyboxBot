import logging
from typing import TYPE_CHECKING

import dis_snek
from dis_snek import InteractionContext, Modal, ParagraphText, Scale, ShortText, check, slash_str_option, subcommand

import utils.misc as utils
from scales.permissions import Permissions

if TYPE_CHECKING:
    from main import Bot

logger = logging.getLogger(__name__)


class MiscScale(Scale):
    bot: "Bot"

    @check(Permissions.check_manager)
    @subcommand(base="say", name="as_bot")
    async def echo(
        self,
        ctx: InteractionContext,
        message: slash_str_option(description="Message content to send") = None,
    ):
        """Sends message from account of this"""
        if message is None:
            modal = Modal(
                "Message to send",
                components=[
                    ParagraphText(
                        label="Message text",
                        custom_id="content",
                        placeholder="Message text to send",
                        required=True,
                    )
                ],
            )
            await ctx.send_modal(modal)
            # Replacing context with modal context here
            ctx = await ctx.bot.wait_for_modal(modal, timeout=15 * 60)
            message = ctx.kwargs["content"]

            await ctx.defer(ephemeral=True)
        else:
            await ctx.defer(ephemeral=True)

        await ctx.channel.send(content=message)

        embed = dis_snek.Embed(
            color=dis_snek.FlatUIColors.EMERLAND,
            description="Message sent",
        )
        await ctx.send(embed=embed)

    @check(Permissions.check_manager)
    @subcommand(base="say", name="as_anything")
    async def webhook_say(
        self,
        ctx: InteractionContext,
        message: slash_str_option(description="Message content to send") = None,
        username: slash_str_option(description="Username for sending fake message") = None,
        avatar_url: slash_str_option(description="Avatar URL for sending fake message") = None,
    ):
        """Sends message from "fake" user with specified avatar and username"""
        if message is None:
            modal = Modal(
                "Fake message to send",
                components=[
                    ShortText(
                        label="Fake username",
                        custom_id="username",
                        placeholder="Username for sending fake message",
                        value=username or dis_snek.MISSING,
                        required=False,
                    ),
                    ShortText(
                        label="Fake avatar URL",
                        custom_id="avatar_url",
                        placeholder="Avatar URL for sending fake message",
                        value=username or dis_snek.MISSING,
                        required=False,
                    ),
                    ParagraphText(
                        label="Message text",
                        custom_id="content",
                        placeholder="Fake message text to send",
                        required=True,
                    ),
                ],
            )
            await ctx.send_modal(modal)
            # Replacing context with modal context here
            ctx = await ctx.bot.wait_for_modal(modal, timeout=15 * 60)
            username = ctx.kwargs["username"]
            avatar_url = ctx.kwargs["avatar_url"]
            message = ctx.kwargs["content"]

            await ctx.defer(ephemeral=True)
        else:
            await ctx.defer(ephemeral=True)

        webhook = await ctx.channel.create_webhook(name="Fractal bot webhook")
        try:
            await webhook.send(
                content=message,
                username=username or ctx.guild.me.display_name,
                avatar_url=avatar_url or ctx.guild.me.display_avatar.url,
            )

            embed = dis_snek.Embed(
                color=dis_snek.FlatUIColors.EMERLAND,
                description="Message sent",
            )
            await ctx.send(embed=embed)

        finally:
            await webhook.delete()


def setup(bot):
    MiscScale(bot)
