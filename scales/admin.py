import dis_snek
from dis_snek import Scale, InteractionContext, AutocompleteContext
from dis_snek import subcommand, slash_str_option, check
from beanie import init_beanie

from scales.permissions import Permissions
from utils.fuzz import fuzzy_autocomplete, fuzzy_find
import logging

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from main import Bot

logger = logging.getLogger(__name__)


class Admin(Scale):
    bot: "Bot"

    @check(Permissions.check_admin)
    @subcommand("bot", name="reload")
    async def reload(self, ctx: InteractionContext,
                     extension: slash_str_option(description="Extension to reload", required=False, autocomplete=True) = None,
                     ):
        await ctx.defer(ephemeral=True)
        if extension:
            extensions = [extension]
        else:
            extensions = list(self.bot.get_extensions())

        loaded = []
        errors = {}
        for extension in extensions:
            try:
                self.bot.reload_extension(extension)
            except Exception as e:
                errors[extension] = e
            else:
                loaded.append(extension)
        msg = ""
        if loaded:
            msg = f"Successfully reloaded extensions: {', '.join(f'**{name}**' for name in loaded)}"
        if errors:
            msg += "\nWere unable to load extensions due to following errors:\n"
            msg += "\n".join(f"**{name}**: {error}" for name, error in errors.items())

        try:
            await init_beanie(database=self.bot.db.db_name, document_models=self.bot.models)
        except Exception as e:
            msg += f"\nWere unable to synchronize database models due to error: {e}"
        else:
            msg += "\nSuccessfully synchronized database models"

        try:
            await self.bot.synchronise_interactions()
        except Exception as e:
            msg += f"\nWere unable to synchronize interactions due to error: {e}"
        else:
            msg += "\nSuccessfully synchronized interactions"

        await ctx.send(msg)

    @reload.autocomplete("extension")
    async def _reload_extension(self, ctx: AutocompleteContext, extension, **kwargs):
        extensions = list(self.bot.get_extensions())
        choices = [choice[0] for choice in fuzzy_autocomplete(extension, extensions)]
        await ctx.send(choices)

    @subcommand("bot", name="test")
    async def test(self, ctx: InteractionContext, test: slash_str_option("123")):
        from utils.modals import generate_modal
        from scales.roles import RoleGroup
        model = await RoleGroup.find_one(RoleGroup.name == "Managed")
        result = await generate_modal(ctx, RoleGroup)
        print(result)

    @test.autocomplete("test")
    async def _test(self, ctx: dis_snek.AutocompleteContext, **kwargs):
        # import datetime
        now: dis_snek.Timestamp = dis_snek.Timestamp.now()

        await ctx.send([str(now)])


def setup(bot):
    Admin(bot)
