import logging
from typing import TYPE_CHECKING, Any, List, Optional, Tuple

import dis_snek
from beanie import Indexed, Link
from beanie.operators import In, Set
from bson.objectid import ObjectId
from dis_snek import (
    MISSING,
    Absent,
    AutocompleteContext,
    Button,
    ComponentContext,
    EmbedField,
    Guild,
    InteractionContext,
    Modal,
    ModalContext,
    ParagraphText,
    Role,
    Scale,
    Select,
    SelectOption,
    ShortText,
    check,
    component_callback,
    slash_bool_option,
    slash_int_option,
    slash_role_option,
    slash_str_option,
    slash_user_option,
    subcommand,
    tasks,
)
from pydantic import Field

# from pydantic import BaseModel
import scales.permissions
import utils.log as log_utils
import utils.misc as utils
from scales.permissions import Permissions, can_manage_role
from utils import modals as modal_utils
from utils.db import Document
from utils.fuzz import fuzzy_autocomplete, fuzzy_find
from utils.misc import ResponseStatusColors, send_with_embed

if TYPE_CHECKING:
    from main import Bot

logger: log_utils.BotLogger = logging.getLogger(__name__)  # type: ignore


class RoleGroup(Document):
    guild_id: Indexed(int) = Field(editable=False)
    name: Indexed(str)
    priority: int = Field(gt=-1)
    color: Optional[str] = None
    exclusive_roles: bool = False
    description: str = ""

    def __init__(self, *, name, **kwargs):
        name = utils.convert_to_db_name(name)
        super(RoleGroup, self).__init__(name=name, **kwargs)

    @property
    def display_name(self):
        return utils.convert_to_display_name(self.name)

    # status: done, tested
    @staticmethod
    async def validate_name(group_name: str, guild: dis_snek.Guild):
        if await RoleGroup.find_one(RoleGroup.name == group_name, RoleGroup.guild_id == guild.id):
            raise utils.BadBotArgument(f"Role group with name '{group_name}' already exists on this server!")


class BotRole(Document):
    role_id: Indexed(int)
    name: str  # Just to make it easier to look at raw DB data
    group: Link[RoleGroup]
    assignable: bool = False
    description: str = ""
    emoji: Optional[str] = None

    @staticmethod
    def group_request(group: RoleGroup):
        return {"group.$id": group.id}

    @staticmethod
    def group_link_request(group_link: Link):
        return {"group.$id": group_link.ref.id}


class RoleSelectorMessage(Document):
    message_id: Indexed(int)
    channel_id: int
    group: Link[RoleGroup]

    @staticmethod
    def group_request(group: RoleGroup):
        return {"group.$id": group.id}


def to_option_value(option: str):
    return option.replace(" ", "_")


class RoleSelector(Scale):
    bot: "Bot"

    delete_roles_option = "< Delete roles >"

    # def __init__(self, client):
    # self.add_scale_check(dis_snek.guild_only())

    @dis_snek.listen()
    async def on_startup(self) -> None:
        sync_task = tasks.Task(self.sync_roles_task, tasks.triggers.IntervalTrigger(hours=6))
        sync_task.start()
        await sync_task()

    # status: done, tested
    @check(Permissions.check_manager)
    @subcommand(
        base="role",
        name="create_selector",
        base_description="Roles commands",
        description="Create a selector, that allows to assign roles instead of the default /role assign command",
    )
    async def create_static(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group of roles to make selector with", required=True, autocomplete=True),
    ):
        await ctx.defer()
        group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)
        components = await self.create_selector_components_for_group(group)
        embed = await self.create_selector_embed_for_group(group=group, guild=ctx.guild)

        message = await ctx.send(embed=embed, components=components)
        message_tracker = RoleSelectorMessage(
            message_id=message.id,
            channel_id=message.channel.id,
            group=group,
        )
        await message_tracker.insert()
        logger.command(ctx, f"Created a selector for group {group.display_name} in {ctx.channel}")

    @create_static.autocomplete("group")
    async def _create_static_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group, hide_empty=True)

    # status: done, not tested
    @component_callback("static_role_select")
    async def give_roles_static(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)

        message_id = int(ctx.data["message"]["id"])
        message_tracker = await RoleSelectorMessage.find_one(RoleSelectorMessage.message_id == message_id)
        # channel_id = message_tracker.channel_id
        # message = await self.bot.cache.get_message(channel_id, message_id)

        choices = ctx.values
        choices = [ObjectId(choice) for choice in choices]
        to_add = []
        to_remove = []
        async for db_role in BotRole.find(BotRole.group_link_request(message_tracker.group)):
            role = await ctx.guild.fetch_role(db_role.role_id)
            if db_role.id in choices:
                if role not in ctx.author.roles:
                    to_add.append(role)
            else:
                if role in ctx.author.roles:
                    to_remove.append(role)

        for role in to_remove:
            try:
                await self.try_remove_role(
                    requester=ctx.author,
                    target=ctx.author,
                    role=role,
                    reason=f"Self-removed using {self.bot.user.display_name}'s selector",
                )
            except Exception as e:
                logger.warning(f"Exception while removing role {role} from {ctx.author} via static select: {e}")

        for role in to_add:
            try:
                await self.try_assign_role(
                    requester=ctx.author,
                    target=ctx.author,
                    role=role,
                    reason=f"Self-assigned using {self.bot.user.display_name}'s selector",
                )
            except Exception as e:
                logger.warning(f"Exception while assigning role {role} to {ctx.author} via static select: {e}")

        added = " ".join([role.mention for role in to_add])
        removed = " ".join([role.mention for role in to_remove])
        if not added and not removed:
            await send_with_embed(
                ctx,
                embed_text="No changes in roles were made",
                status_color=ResponseStatusColors.INCORRECT_INPUT,
            )
        elif added and not removed:
            await send_with_embed(ctx, embed_title="Added roles", embed_text=added)
        elif removed and not added:
            await send_with_embed(ctx, embed_title="Removed roles", embed_text=removed)
        else:
            results = dis_snek.Embed(title="Changes in roles", color=ResponseStatusColors.SUCCESS.value)
            results.add_field(name="Removed roles", value=removed, inline=False)
            results.add_field(name="Added roles", value=added, inline=False)
            await ctx.send(embed=results)
        logger.command(ctx, f"Used role selector, added roles [{added}], removed roles [{removed}]")

    # status: done, not tested
    @component_callback("static_role_clear_roles")
    async def clear_roles_static(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)
        message_id = int(ctx.data["message"]["id"])
        message_tracker = await RoleSelectorMessage.find_one(RoleSelectorMessage.message_id == message_id)
        group = await message_tracker.group.fetch()

        removed_roles = await self.try_remove_group(
            ctx.author,
            ctx.author,
            group,
            reason=f"Self-removed using {self.bot.user.display_name}'s selector",
        )
        if removed_roles:
            response = (
                f"Removed {len(removed_roles)} roles"
                if len(removed_roles) > 1
                else f"Removed role {removed_roles[0].mention}"
            )
            await send_with_embed(ctx, embed_title="Removed roles", embed_text=response, ephemeral=True)
            logger.command(
                ctx,
                f"Used role selector, removed roles [{' '.join(role.mention for role in removed_roles)}]",
            )
        else:
            await send_with_embed(
                ctx,
                "You don't have any role from this group",
                status_color=ResponseStatusColors.INCORRECT_INPUT,
                ephemeral=True,
            )
            logger.command(ctx, f"Used role selector, no roles were removed")

    # Status: done, tested
    @subcommand(base="role", name="list")
    async def role_list(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group to display", autocomplete=True, required=False) = None,
        only_assignable: slash_bool_option("Show only assignable roles", required=False) = True,
    ):
        """Shows list of all roles available to you. Roles are grouped by role group"""
        await ctx.defer(ephemeral=False)

        embed = utils.get_default_embed(ctx.guild, "Available roles:", ResponseStatusColors.INFO)
        if group:
            groups = [await self.role_group_find(group, ctx.guild)]
        else:
            groups = await RoleGroup.find(RoleGroup.guild_id == ctx.guild_id).to_list()

        if not only_assignable:
            only_assignable = not await Permissions.is_manager(ctx.author)

        db_group: RoleGroup
        for db_group in groups:
            fields = await self.get_roles_list_fields(group=db_group, guild=ctx.guild, only_assignable=only_assignable)

            # don't display "empty" groups to users unless this specific group was requested
            if fields:
                embed.fields += fields
            elif group:
                embed.add_field(name=db_group.display_name, value="*No assignable roles available*")

        if not embed.fields:
            embed.description = "No roles, at all 🥲"

        await ctx.send(embed=embed)  # allowed_mentions=dis_snek.AllowedMentions.none()

    @role_list.autocomplete("group")
    async def _role_list_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # TODO: maybe add some generic database debug command instead of role

    # Status: done, tested
    @subcommand(base="role", name="assign")
    async def role_assign(
        self,
        ctx: InteractionContext,
        role: slash_role_option("Role to assign", required=True),
        member: slash_user_option("Member to assign the role to", required=False) = None,
    ):
        """Assigns a selected role to you or selected obj"""
        await ctx.defer(ephemeral=True)

        print(utils.ResponseStatusColors.ERROR.value)

        target = member or ctx.author
        if role in target.roles:
            await send_with_embed(
                ctx,
                f"Looks like {utils.member_mention(ctx, target).lower()} already have {role.mention} role ;)",
                status_color=ResponseStatusColors.INCORRECT_INPUT,
            )
            return

        # all checks are done inside try_assign_role
        await self.try_assign_role(
            requester=ctx.author,
            target=target,
            role=role,
            reason=f"Assigned by {ctx.author.display_name} by using bot command",
        )
        await send_with_embed(
            ctx,
            f"Assigned role {role.mention} to {utils.member_mention(ctx, target).lower()}",
        )
        logger.command(ctx, f"Assigned role {role} to {target}")

    # Status: done, tested
    @subcommand(base="role", name="unassign")
    async def role_unassign(
        self,
        ctx: InteractionContext,
        role: slash_role_option("Role to assign", required=True),
        member: slash_user_option("Member to assign the role to", required=False) = None,
    ):
        """Removes a role from you or selected obj"""
        await ctx.defer(ephemeral=True)

        target = member or ctx.author
        if role not in target.roles:
            await send_with_embed(
                ctx,
                f"Looks like {utils.member_mention(ctx, target).lower()} don't have {role.mention} role anyways ;)",
                status_color=ResponseStatusColors.INCORRECT_INPUT,
            )
            return

        await self.try_remove_role(
            requester=ctx.author,
            target=target,
            role=role,
            reason=f"Removed by {ctx.author.nickname} by using bot command",
        )
        await send_with_embed(
            ctx,
            f"Removed role {role.mention} from {utils.member_mention(ctx, target).lower()}",
        )
        logger.command(ctx, f"Removed role {role} from {target}")

    # Status: done, tested
    @check(Permissions.check_manager)
    @subcommand(base="manage", subcommand_group="roles", name="track_one")
    async def role_track_role(
        self,
        ctx: InteractionContext,
        role: slash_role_option("Role to manage", required=True),
        group: slash_str_option("Group to add new role to", autocomplete=True, required=False) = None,
        description: slash_str_option("Description for this role", required=False) = "",
        emoji: slash_str_option("Emoji for this role", required=False) = None,
        assignable: slash_bool_option(
            "Should be users allowed to assign this role for themselves", required=False
        ) = False,
    ):
        """Allows bot to manage this role and assign it to the server members by request"""
        await ctx.defer(ephemeral=True)

        if group:
            group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)

        await self.track_role(
            ctx,
            role,
            group,
            description=description,
            emoji=emoji,
            assignable=assignable,
        )

        group_name = group.display_name if group else self.bot.config.default_manage_group
        await send_with_embed(
            ctx,
            f"Role {role.mention} is managed by bot from now and put in the group '{group_name}'",
        )

    # Status: done, tested
    @role_track_role.autocomplete("group")
    async def _role_track_role_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # Status: done, not tested
    @check(Permissions.check_manager)
    @subcommand(base="manage", subcommand_group="roles", name="track_all")
    async def role_track_all(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group to add new role to", autocomplete=True, required=False) = None,
    ):
        """Allows bot to manage all roles, that it is allowed to manage by discord"""
        # await ctx.defer(ephemeral=True)
        if group:
            group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)

        to_track = [role for role in ctx.guild.roles if (await self.check_role_for_tracking(role))[0]]

        if not to_track:
            raise utils.BadBotArgument("Sorry, but there are no roles available for tracking")

        modal = Modal(
            title="Track roles",
            components=[
                ShortText(
                    label="Instructions",
                    value="Remove roles from list below to not track them",
                    required=False,
                ),
                ParagraphText(
                    label="List all roles to track",
                    custom_id="roles_names",
                    value="\n".join(role.name for role in to_track),
                ),
            ],
        )

        await ctx.send_modal(modal)

        response = await self.bot.wait_for_modal(modal, timeout=15 * 60)
        await response.defer()
        new_roles = []

        roles_names = response.kwargs["roles_names"].split("\n")
        roles_names = [name.strip() for name in roles_names]
        for role in to_track:
            if role.name in roles_names:
                try:
                    await self.track_role(ctx, role, group)
                except utils.BadBotArgument:
                    continue
                else:
                    new_roles.append(role)

        group_name = group.display_name if group else self.bot.config.default_manage_group

        embed = dis_snek.Embed(title="Tracking roles")
        embed.add_field(name="Group", value=group_name)
        embed.add_field(name="Added", value=f"{len(new_roles)} roles")
        # - 1 for automatic skip to exclude @everyone????? desman, plz check
        embed.add_field(
            name="Skipped",
            value=f"**{len(ctx.guild.roles) - len(to_track) - 1}** roles skipped automatically\n"
            f"**{len(to_track) - len(new_roles)}** roles skipped by selection in modal",
        )
        await response.send(embed=embed)

    # Status: done, tested
    @role_track_all.autocomplete("group")
    async def _role_track_all_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # status: done, tested
    @check(Permissions.check_manager)
    @subcommand(base="manage", subcommand_group="roles", name="stop_tracking")
    async def role_stop_track_role(
        self,
        ctx: InteractionContext,
        role: slash_role_option("Role to stop tracking", required=True),
    ):
        """Stops bot from managing the role"""
        await ctx.defer(ephemeral=True)
        try:
            await self.stop_role_tracking(role.id)
        except utils.BadBotArgument:
            await send_with_embed(
                ctx,
                f"Role {role.mention} is not in the database and not managed by bot",
                status_color=ResponseStatusColors.INCORRECT_INPUT,
            )
            return
        await send_with_embed(ctx, f"Role {role.mention} is not managed by bot anymore")

    # Status: done, tested
    @subcommand(base="manage", subcommand_group="roles", name="sync_roles")
    async def role_sync_db(self, ctx: InteractionContext):
        """Removes all roles, that are not available anymore, from the database, updates internal role names"""
        await ctx.defer(ephemeral=True)

        deleted, renamed = await self.sync_roles_for_guild(ctx.guild)

        report = f"Synced db roles for {ctx.guild.name}."
        if deleted:
            report = report + f" Deleted {len(deleted)} roles."
        if renamed:
            report = report + f" Renamed {len(renamed)} roles."
        if not deleted and not renamed:
            report = report + " No changes in database"
        await send_with_embed(ctx, report)

    # status: done, tested
    @dis_snek.listen()
    async def on_role_delete(self, event):
        logger.info(f"Reacting on role deletion in {event.guild.name}")
        await self.sync_roles_for_guild(event.guild)

    # status: done, tested
    @dis_snek.listen()
    async def on_role_update(self, event):
        logger.info(f"Reacting on role update in {event.guild.name}")
        await self.sync_roles_for_guild(event.guild)

    # status: done, not tested
    async def sync_roles_task(self):
        logger.info("Running regular sync roles task")
        for guild in self.bot.guilds:
            await self.sync_roles_for_guild(guild)

    # Status: done, tested
    @subcommand(base="manage", subcommand_group="roles", name="edit")
    async def role_edit(
        self,
        ctx: InteractionContext,
        role: slash_role_option("Role to edit", required=True),
        group: slash_str_option("Group to add new role to", autocomplete=True, required=False) = None,
        description: slash_str_option("Description for this role", required=False) = None,
        emoji: slash_str_option("Emoji for this role", required=False) = None,
        assignable: slash_bool_option(
            "Should be users allowed to assign this role for themselves", required=False
        ) = None,
    ):
        """Allows editing database properties of the role"""
        await ctx.defer(ephemeral=True)
        if group is None and description is None and emoji is None and assignable is None:
            raise utils.BadBotArgument("Nothing to change!")

        db_role = await BotRole.find_one(BotRole.role_id == role.id, fetch_links=True)
        if not db_role:
            raise utils.BadBotArgument(f"Role {role.mention} is not in the database and not managed by bot")

        changes: dict[str, tuple[Any, Any]] = dict()
        old_group = None
        if group is not None:
            group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)
            if db_role.group != group:
                changes["Group"] = (db_role.group.display_name, group.display_name)
                old_group = db_role.group
                db_role.group = group

        if description is not None:
            if description.lower() == "none":
                description = ""
            if description != db_role.description:
                changes["Description"] = (db_role.description, description)
                db_role.description = description

        if emoji:
            if not utils.is_emoji(emoji):
                raise utils.BadBotArgument(f"'{emoji}' is not a valid emoji!")
            if db_role.emoji != emoji:
                changes["Emoji"] = (db_role.emoji, emoji)
                db_role.emoji = emoji

        if assignable is not None and db_role.assignable != assignable:
            changes["Assignable"] = (str(db_role.assignable), str(assignable))
            db_role.assignable = assignable

        if changes:
            logger.db(f"Updating DB role {db_role.name}")

            embed = dis_snek.Embed(
                title=f"Updated the role {role.name} with new parameters",
                color=ResponseStatusColors.SUCCESS,
            )
            for name, (before, after) in changes.items():
                embed.add_field(name, f"{before or '<EMPTY>'} → {after or '<EMPTY>'}")
                logger.db(f"Changing {name}: '{before}' → '{after}'")

            await db_role.save()
            if old_group is not None:
                await self.on_group_roles_change(old_group)
                await self.on_group_roles_change(group)

            await ctx.send(embed=embed)
        else:
            await send_with_embed(
                ctx,
                f"Role {role.mention} already have this parameters set, no changes were made",
                status_color=ResponseStatusColors.INCORRECT_INPUT,
            )

    @role_edit.autocomplete("group")
    async def _role_edit_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx=ctx, group=group)

    # status: done, tested
    @subcommand(base="manage", subcommand_group="groups", name="add")
    async def group_add(
        self,
        ctx: InteractionContext,
        name: slash_str_option("group name", required=True),
        color: slash_str_option("Color theme of the group", autocomplete=True, required=False) = None,
        exclusive_roles: slash_bool_option(
            "Should the person be allowed to have only one role from this group",
            required=False,
        ) = False,
        description: slash_str_option("group description", required=False) = "",
    ):
        """Adds a role group with selected name and description"""
        await ctx.defer(ephemeral=True)
        if color:
            utils.validate_color(color)
        group = await self.create_new_role_group(
            guild=ctx.guild,
            name=name,
            color=color,
            exclusive_roles=exclusive_roles,
            description=description,
        )
        await send_with_embed(ctx, f"Created role group '{group.display_name}'")

    # Status: done, tested
    @group_add.autocomplete("color")
    async def _group_add_color(self, ctx: AutocompleteContext, color: str, **kwargs):
        return await utils.color_autocomplete(ctx, color)

    # status: done, tested
    @subcommand(base="manage", subcommand_group="groups", name="delete")
    async def group_delete(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group to delete", autocomplete=True, required=True),
        transfer_group: slash_str_option(
            "Roles from the deleted group will be moved here",
            autocomplete=True,
            required=True,
        ),
    ):
        """Deletes selected group and moves roles from it to the other group or deletes it"""
        await ctx.defer(ephemeral=True)
        group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)
        if transfer_group == to_option_value(self.delete_roles_option):
            transfer_group = None

        if transfer_group:
            transfer_group = await self.role_group_find(transfer_group, ctx.guild, use_fuzzy_search=False)
        if group == transfer_group:
            raise utils.BadBotArgument(
                f"Transfer group and group to delete are the same! Use '{self.delete_roles_option}' option to delete roles with a group"
            )

        group_roles = BotRole.find(BotRole.group_request(group))
        if transfer_group:
            # await group_roles.update(Set({BotManagedRole.group: transfer_group}))  # TODO wait for odmantic fix
            logger.db(f"Moving roles from {group.display_name} to {transfer_group.display_name} on group deletion")
            await group_roles.update(Set(BotRole.group_request(transfer_group)))
            await self.on_group_roles_change(transfer_group)
        else:
            logger.db(f"Deleting roles from {group.display_name} on group deletion")
            await group_roles.delete()

        await self.mark_selectors_deleted(group)
        logger.db(f"Deleting role group {group.display_name}")
        await group.delete()
        roles_action_text = f"moved to '{transfer_group.display_name}'" if transfer_group else "untracked"
        await send_with_embed(
            ctx,
            f"Group '{group.display_name}' was deleted, roles from '{group.display_name}' were {roles_action_text}",
        )

    # Status: done, tested
    @group_delete.autocomplete("group")
    async def _group_delete_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # Status: done, tested
    @group_delete.autocomplete("transfer_group")
    async def _group_delete_transfer_group(self, ctx: AutocompleteContext, transfer_group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, transfer_group, additional_options=[self.delete_roles_option])

    # Status: done, tested
    @subcommand(base="manage", subcommand_group="groups", name="edit")
    async def group_edit(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group to edit", autocomplete=True, required=True),
        priority: slash_int_option("Priority of the group (affects ordering)", required=False) = None,
        name: slash_str_option("Name of the group", required=False) = None,
        color: slash_str_option("Color theme of the group", autocomplete=True, required=False) = None,
        exclusive_roles: slash_bool_option(
            "Should the person be allowed to have only one role from this group",
            required=False,
        ) = None,
        description: slash_str_option("Description of this group. Use 'None' to delete the description") = None,
    ):
        """Edits parameters of the existing group"""
        await ctx.defer(ephemeral=True)

        group = await self.role_group_find(group_name=group, guild=ctx.guild, use_fuzzy_search=False)
        old_name = group.display_name

        if priority and priority != group.priority:
            logger.db(f"Updating group {group.display_name} priority: '{group.priority}' -> '{priority}'")
            await self.ensure_group_priority_free(ctx.guild, priority)
            group.priority = priority
        if name:
            name = utils.convert_to_db_name(name)
            if name != group.name:
                await RoleGroup.validate_name(group_name=name, guild=ctx.guild)
                logger.db(f"Updating group {group.display_name} name: '{group.name}' -> '{name}'")
                group.name = name
        if color and color != group.color:
            utils.validate_color(color)
            logger.db(f"Updating group {group.display_name} color: '{group.color}' -> '{color}'")
            group.color = color
        if exclusive_roles and exclusive_roles != group.exclusive_roles:
            logger.db(
                f"Updating group {group.display_name} exclusivity: '{group.exclusive_roles}' -> '{exclusive_roles}'"
            )
            group.exclusive_roles = exclusive_roles
        if description:
            if description.lower() == "none":
                description = ""
            if description != group.description:
                logger.db(f"Updating group {group.display_name} description: '{group.description}' -> '{description}'")
                group.description = description

        logger.db(f"Updating role group {group.display_name}")
        await group.save()
        await send_with_embed(ctx, f"Group {old_name} was successfully updated")
        await self.on_group_roles_change(group)

    # Status: done, tested
    @group_edit.autocomplete("group")
    async def _group_edit_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # Status: done, tested
    @group_edit.autocomplete("color")
    async def _group_edit_color(self, ctx: AutocompleteContext, color: str, **kwargs):
        return await utils.color_autocomplete(ctx, color)

    # Status: not done
    # TODO DO IT WITH MODAL
    @subcommand(base="role", subcommand_group="group", name="edit_roles", description="11")
    async def group_edit_roles(self, ctx: InteractionContext):
        pass

    # status: done, tested
    async def track_role(
        self,
        ctx: InteractionContext,
        role: Role,
        group: Optional[RoleGroup] = None,
        description: str = "",
        emoji: Optional[str] = None,
        assignable: bool = False,
    ):
        """Adds role to the internal database"""
        can_track, reason = await self.check_role_for_tracking(role)
        if not can_track:
            raise utils.BadBotArgument(reason)

        if emoji and not utils.is_emoji(emoji):
            raise utils.BadBotArgument(f"'{emoji}' is not a valid emoji")

        if not group:
            # when no group specified, try to find DEFAULT group for this server or create it
            group = await RoleGroup.find_one(
                RoleGroup.name == self.bot.config.default_manage_group,
                RoleGroup.guild_id == ctx.guild_id,
            )
            if not group:
                group = await self.create_new_role_group(
                    guild=ctx.guild,
                    name=self.bot.config.default_manage_group,
                    description=self.bot.config.default_manage_group_desc,
                )

        # Check that we can add more roles to the group, if that's not a default group
        if group.name != self.bot.config.default_manage_group:
            existing_roles_num = await BotRole.find(BotRole.group_request(group)).count()
            if existing_roles_num >= self.bot.config.max_roles_in_group:
                raise utils.BadBotArgument(
                    f"Group {group.display_name} has too much roles already! "
                    f"One group can't have more than {self.bot.config.max_roles_in_group} roles"
                )

        db_role = BotRole(
            role_id=role.id,
            group=group,
            name=role.name,
            description=description,
            emoji=emoji,
            assignable=assignable,
        )
        logger.db(f"Adding tracked role {db_role.name} in group {group.display_name}")
        await db_role.insert()
        await self.on_group_roles_change(group)

    # status: done, tested
    async def stop_role_tracking(self, role_id: int):
        """Removes role from internal database by ID"""
        db_role = await BotRole.find_one(BotRole.role_id == role_id, fetch_links=True)
        if db_role:
            logger.db(f"Removing tracked role {db_role.name}")
            await db_role.delete()
            await self.on_group_roles_change(db_role.group)
        else:
            raise utils.BadBotArgument(f"Role with ID {role_id} is not managed by bot")

    # status: done, tested
    @staticmethod
    async def create_new_role_group(
        guild: dis_snek.Guild,
        name: str,
        color: Optional[str] = None,
        exclusive_roles: bool = False,
        description: str = "",
    ):
        """Creates a new role group for the guild, checks for collisions"""
        name = utils.convert_to_db_name(name)
        await RoleGroup.validate_name(group_name=name, guild=guild)

        priority = await RoleGroup.find(RoleGroup.guild_id == guild.id).max("priority") or -1
        priority = priority + 1
        group = RoleGroup(
            guild_id=guild.id,
            priority=priority,
            name=name,
            color=color,
            exclusive_roles=exclusive_roles,
            description=description,
        )
        logger.db(f"Adding group {group.display_name}")
        await group.insert()
        return group

    # status: done, not tested
    @staticmethod
    async def role_group_autocomplete(
        ctx: AutocompleteContext,
        group: str,
        additional_options: List[str] = None,
        hide_empty: bool = False,
    ):
        """Autocompletes role groups request with optional additional options"""
        groups = await RoleGroup.find(RoleGroup.guild_id == ctx.guild_id).to_list()
        if hide_empty:
            # TODO: optimize plz maybe. distinct? cross-query? lol you wish
            group_ids = [ObjectId(group.id) for group in groups]
            roles_in_groups = await BotRole.find(In("group.$id", group_ids)).to_list()
            group_ids = {role.group.ref.id for role in roles_in_groups}
            groups = await RoleGroup.find(In(RoleGroup.id, group_ids)).to_list()
        groups_list = []
        if additional_options:
            groups_list.extend(additional_options)
        groups_list.extend(g.display_name for g in groups)

        results = fuzzy_autocomplete(group, groups_list)
        results = [value[0] for value in results]
        await ctx.send(results)

    # status: done, tested
    @staticmethod
    async def role_group_find(group_name: str, guild: dis_snek.Guild, use_fuzzy_search: bool = True) -> RoleGroup:
        """
        Method to get RoleGroup object from data received from group slash options.
        Raises utils.BadBotArgument if fails to find it
        """
        group_name = utils.convert_to_db_name(group_name)

        # try and get role group with exact same name
        group = await RoleGroup.find_one(RoleGroup.name == group_name, RoleGroup.guild_id == guild.id)
        if group is None:  # user gave us incorrect or incomplete role name
            if not use_fuzzy_search:
                raise utils.BadBotArgument(f"Can't find a group '{group_name}' for this server!")

            groups = await RoleGroup.find(RoleGroup.guild_id == guild.id).to_list()
            groups_list = [g.name for g in groups]
            fuzzy_group_name = fuzzy_find(group_name, groups_list)
            if fuzzy_group_name is None:
                raise utils.BadBotArgument(f"Can't find a group '{group_name}' for this server!")

            group = await RoleGroup.find_one(RoleGroup.name == fuzzy_group_name, RoleGroup.guild_id == guild.id)

        return group

    # status: done, tested
    @staticmethod
    async def try_assign_role(
        requester: dis_snek.Member,
        target: dis_snek.Member,
        role: dis_snek.Role,
        reason: Absent[str] = MISSING,
    ):
        """Tries to assign a role to a target. Returns list of unassigned roles as a result or raises utils.BadBotArgument"""
        db_role = await BotRole.find_one(BotRole.role_id == role.id)
        if not db_role:
            raise utils.BadBotArgument(
                f"Trying to assign role {role.mention}, that is not in the database! Probably you shouldn't do it"
            )

        if requester != target or not db_role.assignable:
            if not await can_manage_role(requester, role):
                raise utils.BadBotArgument("Trying to assign role without a permissions to do it!")

        # remove conflicting roles
        group = await db_role.group.fetch()
        if group.exclusive_roles:
            target_roles = {target_role.id: target_role for target_role in target.roles}
            db_roles = BotRole.find(
                BotRole.group_request(group),
                In(BotRole.role_id, list(target_roles.keys())),
            )
            async for db_role in db_roles:
                role_to_remove = target_roles[db_role.role_id]
                logger.important(f"Removing role {role_to_remove} from {target} since it conflicts with {role}")
                await target.remove_role(
                    role_to_remove,
                    reason=f"Removing a conflicting role from group {group.display_name} "
                    f"when assigning '{role.name}' by {requester.display_name}",
                )

        logger.important(f"Adding role {role} to {target} by {requester} request")
        await target.add_role(role, reason=reason)

    # Status: done, tested
    @staticmethod
    async def try_remove_role(
        requester: dis_snek.Member,
        target: dis_snek.Member,
        role: dis_snek.Role,
        reason: Absent[str] = MISSING,
    ):
        db_role = await BotRole.find_one(BotRole.role_id == role.id)
        if not db_role:
            raise utils.BadBotArgument(
                f"Trying to remove role {role.mention}, that is not in the database! Probably you shouldn't do it"
            )

        if requester != target or not db_role.assignable:
            if not await can_manage_role(requester, role):
                raise utils.BadBotArgument("Trying to unassign role without a permissions to do it!")

        logger.important(f"Removing role {role} from {target} by {requester} request")
        await target.remove_role(role, reason=reason)

    # Status: done, not tested
    @classmethod
    async def try_remove_group(
        cls,
        requester: dis_snek.Member,
        target: dis_snek.Member,
        group: RoleGroup,
        reason: Absent[str] = MISSING,
    ) -> list[dis_snek.Role]:
        """Removes all roles from this group from the target"""
        removed = []
        logger.important(f"Removing all roles from group {group.display_name} from {target} by {requester} request")
        async for db_role in BotRole.find(BotRole.group_request(group)):
            if role := await requester.guild.fetch_role(db_role.role_id):
                if role in target.roles:
                    await cls.try_remove_role(requester, target, role, reason)
                    removed.append(role)

        return removed

    # status: done, tested
    async def sync_roles_for_guild(self, guild: dis_snek.Guild):
        """Deletes all roles from DB, that are not in this guild anymore, updates internal name for renamed roles"""
        deleted = set()
        renamed = dict()
        groups_to_update = set()

        logger.info(f"Syncing roles for guild {guild.name}")
        async for group in RoleGroup.find(RoleGroup.guild_id == guild.id):
            # change during iteration
            db_roles = await BotRole.find(BotRole.group_request(group)).to_list()
            for db_role in db_roles:
                role = await guild.fetch_role(db_role.role_id)
                if not role:
                    deleted.add(db_role.name)
                    logger.db(f"Deleting role {db_role.name} for guild {guild.name}, role doesn't exist anymore")
                    groups_to_update.add(db_role.group.ref.id)
                    await db_role.delete()
                elif role.name != db_role.name:
                    renamed[db_role.name] = role.name
                    logger.db(f"Renaming role {db_role.name} to {role.name} for guild {guild.name}")
                    db_role.name = role.name
                    groups_to_update.add(db_role.group.ref.id)
                    await db_role.save()

        for group_id in groups_to_update:
            group = await RoleGroup.find_one(RoleGroup.id == group_id)
            await self.on_group_roles_change(group)

        logger.info(f"Performed roles sync, {len(deleted)} deleted, {len(renamed)} renamed")
        return deleted, renamed

    # status: done, not tested
    @staticmethod
    async def ensure_group_priority_free(guild: dis_snek.Guild, priority: int):
        num_of_groups_with_priority = await RoleGroup.find(
            RoleGroup.guild_id == guild.id, RoleGroup.priority == priority
        ).count()
        if num_of_groups_with_priority > 0:
            logger.db(f"Found groups with priority {priority}, removing them from this priority")

            following_groups = RoleGroup.find(RoleGroup.guild_id == guild.id, RoleGroup.priority > priority)
            logger.db(f"Adding {num_of_groups_with_priority} to the priority of groups highter than {priority}")
            await following_groups.inc({RoleGroup.priority: num_of_groups_with_priority})
            # change during iteration
            groups_to_change = await RoleGroup.find(
                RoleGroup.guild_id == guild.id, RoleGroup.priority == priority
            ).to_list()
            for index, group in enumerate(groups_to_change):
                new_priority = priority + index + 1
                logger.db(f"Updating group {group.display_name} priority: {group.priority} -> {new_priority}")
                group.priority = new_priority
                await group.save()

    # status: done, tested
    @staticmethod
    async def check_role_for_tracking(
        role: dis_snek.Role,
    ) -> Tuple[bool, Optional[str]]:
        if not role:
            return False, f"Invalid role {role}"

        if role.bot_managed or role.premium_subscriber or role.integration or role.default:
            logger.info(f"Skipping role '{role}' as it's system role")
            return False, f"Role {role.mention} is a system role and won't be managed"

        if not await can_manage_role(role.guild.me, role):
            logger.info(f"Skipping role '{role}' as bot cannot manage it")
            return False, f"Bot cannot manage role {role.mention}"

        if await BotRole.find_one(BotRole.role_id == role.id):
            logger.info(f"Skipping role '{role}' as it already exists")
            return False, f"Role {role.mention} already managed by bot"

        return True, None

    # status: done, not tested
    async def on_group_roles_change(self, group: RoleGroup):
        async for selector in RoleSelectorMessage.find(RoleSelectorMessage.group_request(group)):
            selector: RoleSelectorMessage
            try:
                message = await self.bot.cache.fetch_message(selector.channel_id, selector.message_id)
                logger.important(
                    f"Updating selector message in {message.channel}.{message.guild} on group {group.display_name} update"
                )
                await message.edit(components=await self.create_selector_components_for_group(group))
            except dis_snek.errors.SnakeException as e:
                logger.warning(f"Exception during selector update:", exc_info=e)
                continue

    # status: done, not tested
    async def create_selector_components_for_group(self, group: RoleGroup) -> list[list[dis_snek.BaseComponent]]:

        db_roles: List[BotRole] = await BotRole.find(BotRole.group_request(group), BotRole.assignable == True).to_list()
        options = [
            SelectOption(
                label=role.name,
                value=str(role.id),
                description=role.description,
                emoji=role.emoji,
            )
            for role in db_roles
        ]

        if not options:
            raise utils.BadBotArgument(f"No assignable roles available in the group {group.name}")

        if len(options) > self.bot.config.max_roles_in_group:
            raise utils.BadBotArgument(
                f"Select component can display only up to {self.bot.config.max_roles_in_group} roles! "
                f"Please try with smaller group size"
            )
        select = Select(
            options=options,
            min_values=0,
            max_values=1 if group.exclusive_roles else len(options),
            custom_id="static_role_select",
        )
        clear_roles_button = Button(
            style=dis_snek.ButtonStyles.PRIMARY,
            label="Remove role" if group.exclusive_roles else "Remove roles",
            custom_id="static_role_clear_roles",
        )

        components = [[select], [clear_roles_button]]
        return components

    async def mark_selectors_deleted(self, group):
        async for selector in RoleSelectorMessage.find(RoleSelectorMessage.group_request(group)):
            selector: RoleSelectorMessage
            message = await self.bot.cache.fetch_message(selector.channel_id, selector.message_id)
            logger.important(
                f"Marking selector message in {message.channel}.{message.guild} for group {group.display_name} as deleted"
            )
            await message.edit(
                embed=dis_snek.Embed(
                    title=group.display_name,
                    description="Group was deleted, selector is no more",
                    color=utils.ResponseStatusColors.ERROR.value,
                )
            )

    async def get_roles_list_fields(self, group: RoleGroup, guild: Guild, only_assignable: bool = True) -> list[EmbedField]:
        fields = []
        db_roles = BotRole.find(BotRole.group_request(group))
        if only_assignable:
            db_roles.find(BotRole.assignable == True)
        db_role: BotRole

        mentions = []
        async for db_role in db_roles:
            if role := await guild.fetch_role(db_role.role_id):
                emoji = db_role.emoji or self.bot.get_emoji(utils.SystemEmojis.BLANK)
                mentions.append(f"{emoji}{role.mention}")

        if mentions:
            n = 9  # 9 role mentions max in one embed field
            mentions_regrouped = [mentions[i: i + n] for i in range(0, len(mentions), n)]
            for i, mentions_group in enumerate(mentions_regrouped, 1):
                group_len = len(mentions_regrouped)
                name = group.display_name if group_len == 1 else f"{group.display_name} [{i}/{group_len}]"
                field = EmbedField(name=name, value="\n".join(mentions_group), inline=True)
                fields.append(field)

        return fields


def setup(bot):
    RoleSelector(bot)
    bot.add_model(RoleGroup)
    bot.add_model(BotRole)
    bot.add_model(RoleSelectorMessage)
