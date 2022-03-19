import dis_snek
from dis_snek import Scale, InteractionContext, AutocompleteContext
from dis_snek import subcommand, check, is_owner, slash_str_option, slash_user_option

from beanie import Indexed
from utils.db import Document

from typing import Optional, Union, TYPE_CHECKING

from utils import misc as utils
from utils.misc import send_with_embed, ResponseStatusColors
from utils.fuzz import fuzzy_autocomplete


if TYPE_CHECKING:
    from main import Bot


class BotAdmins(Document):
    """People have full access to ALL bots commands"""

    user_id: Indexed(int)


class BotManagers(Document):
    """People have access to all commands related to this guild"""

    member_id: Indexed(int)
    guild_id: int

    can_grant: bool = False


# TODO track roles that grant manager permissions


async def can_manage_role(member: dis_snek.Member, role: dis_snek.Role) -> bool:
    """Checks, if obj have permissions to manage this role"""
    # TODO: direct role comparison behaves weird, so comparing positions for now
    if member.has_permission(dis_snek.Permissions.MANAGE_ROLES) or await Permissions.is_manager(member):
        return member.top_role and member.top_role.position > role.position
    return False


class Permissions(Scale):
    bot: "Bot"

    @check(is_owner())
    @subcommand(base="permissions", subcommand_group="admin", name="grant")
    async def admin_grant(
        self,
        ctx: InteractionContext,
        member: slash_user_option("Member to grant permissions to") = None,
        user_id: slash_str_option("User ID to grant permissions to") = None,
    ):
        """Grants admin permissions to user"""
        await ctx.defer(ephemeral=True)
        user_id = self.get_member_id(member, user_id)

        if await BotAdmins.find_one(BotAdmins.user_id == user_id).exists():
            raise utils.BadBotArgument(
                f"User {await self.fetch_user_mention(ctx, user_id)} already has admin permissions!"
            )

        admin = BotAdmins(user_id=user_id)
        await admin.insert()

        await ctx.send(
            f"Granted admin permissions to user {await self.fetch_user_mention(ctx, user_id)}!"
        )

    @check(is_owner())
    @subcommand(base="permissions", subcommand_group="admin", name="revoke")
    async def admin_revoke(
        self,
        ctx: InteractionContext,
        member: slash_user_option("Member to revoke permissions from") = None,
        user_id: slash_str_option("User ID to revoke permissions from") = None,
    ):
        """Revokes admin permissions from user"""
        await ctx.defer(ephemeral=True)
        user_id = self.get_member_id(member, user_id)

        admin: "BotAdmins" = await BotAdmins.find_one(BotAdmins.user_id == user_id)

        if not admin:
            raise utils.BadBotArgument(
                f"User {await self.fetch_user_mention(ctx, user_id)} does not have admin permissions!"
            )

        await admin.delete()

        await ctx.send(
            f"Revoke admin permissions from user {await self.fetch_user_mention(ctx, user_id)}!"
        )

    @admin_revoke.autocomplete("user_id")
    async def _admin_revoke_user_id(self, ctx: AutocompleteContext, user_id, **kwargs):
        db_admins = await BotAdmins.all().to_list()
        admins = [await self.fetch_user(ctx, admin.user_id) for admin in db_admins]
        admin_tags = [user.tag for user in admins if user is not None]
        admin_ids = [str(admin.user_id) for admin in db_admins]

        # fuzzy_autocomplete()

        choices = [f"{user.id}: {user.tag}" for user in admins]
        await ctx.send()

    @subcommand(base="permissions", subcommand_group="admin", name="check")
    async def admin_check_cmd(
        self,
        ctx: InteractionContext,
        member: slash_user_option("Member to check permissions") = None,
        user_id: slash_str_option("User ID to check permissions") = None,
    ):
        """Check if user has admin permissions"""
        await ctx.defer(ephemeral=True)

        if not (member or user_id):
            user_id = ctx.author.id
        else:
            user_id = self.get_id(member, user_id)

        is_admin = await BotAdmins.find_one(BotAdmins.user_id == user_id).exists()

        await ctx.send(
            f"{await self.fetch_user_mention(ctx, user_id)} {'***is***' if is_admin else 'is ***not***'} admin!"
        )

    @subcommand(base="permissions", subcommand_group="admin", name="list")
    async def admin_list(self, ctx: InteractionContext):
        """Show all users with admin permissions"""
        await ctx.defer(ephemeral=True)

        admins = await BotAdmins.all().to_list()
        embed = utils.get_default_embed(
            ctx.guild, "Bot admins list", utils.ResponseStatusColors.INFO
        )

        embed.add_field(
            name="Admins:",
            value="\n".join(
                [await self.fetch_user_mention(ctx, admin.user_id) for admin in admins]
            )
            or "No admins in database",
        )

        await ctx.send(embed=embed)

    @check(dis_snek.guild_only())
    @subcommand(base="permissions", subcommand_group="manager", name="grant")
    async def manager_grant(
        self,
        ctx: InteractionContext,
        member: slash_user_option("Member to grant permissions to") = None,
        user_id: slash_str_option("User ID to grant permissions to") = None,
        guild_id: slash_str_option("Guild ID to grant permissions to") = None,
    ):
        """Grants guild manager permissions to user"""
        await ctx.defer(ephemeral=True)
        user_id = self.get_member_id(member, user_id)
        guild_id = self.get_id(ctx.guild, guild_id)

        if await BotManagers.find_one(
            BotManagers.member_id == user_id, BotManagers.guild_id == guild_id
        ).exists():
            raise utils.BadBotArgument(
                f"User {await self.fetch_user_mention(ctx, user_id)} "
                f"already has guild manager permissions "
                f"in {await self.fetch_guild_mention(guild_id)}!"
            )

        manager = BotManagers(member_id=user_id, guild_id=guild_id)
        await manager.insert()

        await ctx.send(
            f"Granted guild manager permissions "
            f"to user {await self.fetch_user_mention(ctx, user_id)} "
            f"in {await self.fetch_guild_mention(guild_id)}!"
        )

    @subcommand(base="permissions", subcommand_group="manager", name="revoke")
    async def manager_revoke(
        self,
        ctx: InteractionContext,
        member: slash_user_option("Member to revoke permissions from") = None,
        user_id: slash_str_option("User ID to revoke permissions from") = None,
        guild_id: slash_str_option("Guild ID to revoke permissions from") = None,
    ):
        """Revokes manager permissions from user"""
        await ctx.defer(ephemeral=True)
        user_id = self.get_member_id(member, user_id)
        guild_id = self.get_id(ctx.guild, guild_id)

        manager: "BotManagers" = await BotManagers.find_one(
            BotManagers.member_id == user_id, BotManagers.guild_id == guild_id
        )

        if not manager:
            raise utils.BadBotArgument(
                f"User {await self.fetch_user_mention(ctx, user_id)} "
                f"does not have guild manager permissions"
                f"in {await self.fetch_guild_mention(guild_id)}!"
            )

        await manager.delete()

        await ctx.send(
            f"Revoke guild manager permissions "
            f"from user {await self.fetch_user_mention(ctx, user_id)}"
            f"in {await self.fetch_guild_mention(guild_id)}!"
        )

    # @subcommand(base="permissions", subcommand_group="manager", name="list")
    # async def manager_list(self, ctx: InteractionContext):
    #     """Show all users with admin permissions"""
    #     await ctx.defer(ephemeral=True)
    #
    #     admins = await BotManagers.find(BotManagers.guild_id == ctx.guild_id).to_list()
    #     embed = utils.get_default_embed(ctx.guild, "Guild managers list", utils.ResponseStatusColors.INFO)
    #
    #     embed.add_field(name="Guild managers:",
    #                     value="\n".join([await self.fetch_user_mention(ctx, admin.user_id) for admin in
    #                                      admins]) or "No admins in database")
    #
    #     await ctx.send(embed=embed)

    @classmethod
    async def is_admin(cls, user) -> bool:
        admin = await BotAdmins.find_one(BotAdmins.user_id == user.id)
        return bool(admin)

    @classmethod
    async def check_admin(cls, ctx):
        return await cls.is_admin(ctx.author)

    @classmethod
    async def is_manager(cls, member: dis_snek.Member, can_grant=False) -> bool:
        if await cls.is_admin(member):
            return True

        if await member.guild.get_owner() == member:
            return True

        manager = await BotManagers.find_one(BotManagers.member_id == member.id)
        if not manager:
            return False

        if can_grant and not manager.can_grant:
            return False

        return True

    @classmethod
    async def check_manager(cls, ctx):
        return await cls.is_manager(ctx.author)

    @staticmethod
    def get_id(obj, obj_id):
        if obj:
            obj_id = obj.id
        elif obj_id:
            obj_id = int(obj_id)
        else:
            raise ValueError
        return obj_id

    @classmethod
    def get_member_id(cls, member, user_id):
        try:
            return cls.get_id(member, user_id)
        except ValueError:
            raise utils.BadBotArgument("'member' or 'user_id' should be provided!")

    async def fetch_user(self, ctx, user_id) -> Optional[Union[dis_snek.User, dis_snek.Member]]:
        user = None
        if ctx.guild:
            user = await self.bot.fetch_member(user_id, ctx.guild)

        if user is None:  # obj not found or not in guild
            user = await self.bot.fetch_user(user_id)
        else:
            user = user.user  # obj is found

        return user

    async def fetch_user_mention(self, ctx, user_id):
        user = await self.fetch_user(ctx, user_id)

        if user is None:
            mention = f"`{user_id}`: (User not found)"
        else:
            mention = f"`{user_id}`: {user.mention}"

        return mention

    async def fetch_guild_mention(self, guild_id):
        guild = await self.bot.fetch_guild(guild_id)

        if guild is None:
            mention = f"`{guild_id}`: (Guild not found)"
        else:
            mention = f"`{guild_id}`: {guild.name}"

        return mention


def setup(bot):
    Permissions(bot)
    bot.add_model(BotAdmins)
    bot.add_model(BotManagers)
