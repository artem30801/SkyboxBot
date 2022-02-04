import logging
from typing import Optional, List

import dis_snek
from bson.objectid import ObjectId
from beanie import Indexed, Link
from beanie.operators import Set, In
from dis_snek import (
    Scale,
    InteractionContext,
    ComponentContext,
    AutocompleteContext,
    Select,
    SelectOption,
    Role,
    Color,
    Absent,
    MISSING,
)
from dis_snek import (
    slash_str_option,
    slash_bool_option,
    slash_role_option,
    slash_int_option,
    slash_user_option,
    subcommand,
    component_callback,
)
# from pydantic import BaseModel

from utils.fuzz import fuzzy_autocomplete, fuzzy_find
from utils.db import Document
import utils.misc as utils

logger = logging.getLogger(__name__)

# TODO: Add permissions at some point maybe
# TODO: Test name conversion

class RoleGroup(Document):
    guild_id: Indexed(int)
    name: Indexed(str)
    priority: int
    color: Optional[str] = None
    exclusive_roles: bool = False
    description: str = ""


class BotRole(Document):
    role_id: Indexed(int)
    name: str  # Just to make it easier to look at raw DB data
    group: Link[RoleGroup]
    assignable: bool = False
    description: str = ""
    emoji: Optional[str] = None


class RoleSelectorMessage(Document):
    message_id: Indexed(int)
    channel_id: int
    group: Link[RoleGroup]


def to_option_value(option: str):
    return option.replace(" ", "_")


class RoleSelector(Scale):
    all_roles_option = "< All >"
    delete_roles_option = "< Delete roles >"

    # status: ???
    @subcommand(
        "role",
        name="static_selector",
        base_description="Roles commands",
        description="Make static message",
    )
    async def create_static(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group of roles to make selector with", required=True, autocomplete=True),
    ):
        await ctx.defer()
        group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)
        # roles: List[BotManagedRole] = await BotManagedRole.find(In(BotManagedRole.group, groups)).to_list()
        # TODO respect assignable
        roles: List[BotRole] = await BotRole.find({"group.$id": group.id}).to_list()

        if not roles:
            raise utils.BadBotArgument("No roles are tracked in specified group(s)!")

        options = [SelectOption(label=role.name, value=str(role.id), description=role.description, emoji=role.emoji) for role in roles]

        if len(options) > 25:
            raise utils.BadBotArgument("Select component can display only up to 25 roles! "
                                       "Please try with smaller group size")

        select = Select(
            options=options,
            min_values=0,
            max_values=len(options),
            custom_id="static_role_select",
        )
        # todo add embed and remove all roles button
        # TODO update select component on track/untrack/edit
        message = await ctx.send("Select roles you want to get down below!", components=select)
        message_tracker = RoleSelectorMessage(message_id=message.id,
                                              channel_id=message.channel.id,
                                              group=group,
                                              )
        await message_tracker.insert()
        print(message_tracker)

    @create_static.autocomplete("group")
    async def _create_static_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    @component_callback("static_role_select")
    async def give_roles_static(self, ctx: ComponentContext):
        await ctx.defer(ephemeral=True)

        choices = ctx.values
        message_id = int(ctx.data["message"]["id"])
        message_tracker = await RoleSelectorMessage.find_one(RoleSelectorMessage.message_id == message_id, fetch_links=True)
        # channel_id = message_tracker.channel_id
        # message = await self.bot.cache.get_message(channel_id, message_id)

        added_roles = []
        choices = [ObjectId(choice) for choice in choices]
        async for db_role in BotRole.find(In(BotRole.id, choices)):
            role = await ctx.guild.get_role(db_role.role_id)
            try:
                # todo use our role assigment method?
                await ctx.author.add_role(role, f"Self-assigned using {self.bot.user.display_name}'s selector")
            except dis_snek.DiscordError as e:
                print(e)
                pass  # TODO LOGGER WARNING
            else:
                added_roles.append(role)

        added = "\n".join([role.mention for role in added_roles])
        await ctx.send(f"Added roles {added}")

    # Status: done, tested, needs permissions
    @subcommand(base="role", name="list")
    async def role_list(self, ctx: InteractionContext,
                        group: slash_str_option("Group to display", autocomplete=True, required=False) = None,
                        ):
        """Shows list of all roles available to you. Roles are grouped by role group"""
        embed = utils.get_default_embed(ctx.guild, "Available roles:", self.bot.config.colors["default"])
        if group:
            groups = [await self.role_group_find(group, ctx.guild)]
        else:
            groups = await RoleGroup.find(RoleGroup.guild_id == ctx.guild_id).to_list()

        db_group: RoleGroup
        for db_group in groups:
            # TODO: fix me after adding permissions
            db_roles = BotRole.find({"group.$id": db_group.id}) #, BotRole.assignable == True)
            db_role: BotRole

            mentions = []
            async for db_role in db_roles:
                role = await ctx.guild.get_role(db_role.role_id)
                mentions.append(role.mention)

            # don't display "empty" groups to users unless this specific group was requested
            if mentions:
                n = 9  # 9 role mentions max in one embed field
                mentions_regrouped = [mentions[i:i + n] for i in range(0, len(mentions), n)]
                for i, mentions_group in enumerate(mentions_regrouped, 1):
                    group_len = len(mentions_regrouped)
                    name = db_group.name if group_len == 1 else f"{db_group.name} [{i}/{group_len}]"
                    embed.add_field(name=utils.convert_to_name(name), value="\n".join(mentions_group), inline=True)

            elif group:
                embed.add_field(name=utils.convert_to_name(db_group.name), value="*No assignable roles available*")

        if not embed.fields:
            embed.description = "No roles, at all 🥲"

        await ctx.send("Role list", embeds=embed)  # allowed_mentions=dis_snek.AllowedMentions.none()

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
        member: slash_user_option("Member to assign the role to", required=False) = None
    ):
        """Assigns a selected role to you or selected member"""
        await ctx.defer(ephemeral=True)

        if not utils.can_manage_role(ctx.guild.me, role):
            raise utils.BadBotArgument(f"Sorry, bot cannot manage role {role.mention}! "
                                       f"You probably shouldn't use it")

        target = member or ctx.author
        if role in target.roles:
            await ctx.send(f"Looks like {utils.member_mention(ctx, target).lower()} already have {role.mention} role ;)")
            return

        await self.try_assign_role(requester=ctx.author, target=target, role=role,
                                   reason=f"Assigned by {ctx.author.display_name} by using bot command")
        await ctx.send(f"Assigned role {role.mention} to {utils.member_mention(ctx, target).lower()}")

    # Status: done, tested
    @subcommand(base="role", name="unassign")
    async def role_unassign(
        self,
        ctx: InteractionContext,
        role: slash_role_option("Role to assign", required=True),
        member: slash_user_option("Member to assign the role to", required=False) = None
    ):
        """Removes a role from you or selected member"""
        await ctx.defer(ephemeral=True)

        if not utils.can_manage_role(ctx.guild.me, role):
            raise utils.BadBotArgument(f"Sorry, bot cannot manage role {role.mention}! "
                                       f"You probably shouldn't use it")

        target = member or ctx.author
        if role not in target.roles:
            await ctx.send(f"Looks like {utils.member_mention(ctx, target).lower()} don't have {role.mention} role anyways ;)")
            return

        await self.try_remove_role(requester=ctx.author, target=target, role=role,
                                   reason=f"Removed by {ctx.author.nickname} by using bot command")
        await ctx.send(f"Removed role {role.mention} from {utils.member_mention(ctx, target).lower()}")

    # Status: done, tested
    @subcommand(base="manage", subcommand_group="roles", name="track_one")
    async def role_track_role(
        self,
        ctx: InteractionContext,
        role: slash_role_option("Role to manage", required=True),
        group: slash_str_option("Group to add new role to", autocomplete=True, required=False) = None,
        description: slash_str_option("Description for this role", required=False) = "",
        emoji: slash_str_option("Emoji for this role", required=False) = None,
        assignable: slash_bool_option("Should be users allowed to assign this role for themselves", required=False) = False,
    ):
        """Allows bot to manage this role and assign it to the server members by request"""
        await ctx.defer(ephemeral=True)

        if group:
            group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)

        await self.track_role(ctx, role, group, description=description, emoji=emoji, assignable=assignable)

        group_name = utils.convert_to_name(group.name) if group else utils.convert_to_name(self.bot.config.default_manage_group)
        await ctx.send(f"Role {role.mention} is managed by bot from now and put in the group '{group_name}'")

    # Status: done, tested
    @role_track_role.autocomplete("group")
    async def _role_track_role_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # Status: done, tested
    @subcommand(base="manage", subcommand_group="roles", name="track_all")
    async def role_track_all(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group to add new role to", autocomplete=True, required=False) = None
    ):
        """Allows bot to manage all roles, that it is allowed to manage by discord"""
        await ctx.defer(ephemeral=True)
        if group:
            group = await self.role_group_find(group, ctx.guild, use_fuzzy_search=False)

        new_roles = []
        for role in reversed(ctx.guild.roles[1:]):  # to exclude @everyone by default
            try:
                await self.track_role(ctx, role, group)
            except utils.BadBotArgument:
                continue
            else:
                new_roles.append(role)

        group_name = utils.convert_to_name(group.name) if group else utils.convert_to_name(self.bot.config.default_manage_group)
        await ctx.send(f"Added {len(new_roles)} roles for bot to manage to the group {group_name}"
                       f" ({len(ctx.guild.roles) - 1 - len(new_roles)} skipped)")

    # Status: done, tested
    @role_track_all.autocomplete("group")
    async def _role_track_all_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # status: done, tested
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
            await ctx.send(f"Role {role.mention} is not in the database and not managed by bot")
            return
        await ctx.send(f"Role {role.mention} is not managed by bot anymore")

    # TODO: Also run this periodically and on role deleted event
    # Status: TODO
    @subcommand(base="manage", subcommand_group="roles", name="clear_groups")
    async def role_sync_db(self, ctx: InteractionContext):
        """Removes all roles, that are not available anymore, from the database"""
        pass

    # Status: TODO
    @subcommand(base="role", name="edit", description="Shows list")
    async def role_edit(self, ctx: InteractionContext):
        pass

    # status: done, tested
    @subcommand(base="manage", subcommand_group="groups", name="add_group")
    async def group_add(
        self,
        ctx: InteractionContext,
        name: slash_str_option("group name", required=True),
        color: slash_str_option("Color theme of the group", autocomplete=True, required=False) = None,
        exclusive_roles: slash_bool_option("Should the person be allowed to have only one role from this group", required=False) = False,
        description: slash_str_option("group description", required=False) = "",
    ):
        """Adds a role group with selected name and description"""
        await ctx.defer(ephemeral=True)
        if color:
            self.validate_color(color)
        group = await self.create_new_role_group(guild=ctx.guild, name=name, color=color, exclusive_roles=exclusive_roles, description=description)
        await ctx.send(f"Created role group '{utils.convert_to_name(group.name)}'")

    # Status: done, tested
    @group_add.autocomplete("color")
    async def _group_add_color(self, ctx: AutocompleteContext, color: str, **kwargs):
        return await utils.color_autocomplete(ctx, color)

    # status: done, tested
    @subcommand(base="manage", subcommand_group="groups", name="delete_group")
    async def group_delete(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group to delete", autocomplete=True, required=True),
        transfer_group: slash_str_option("Roles from the deleted group will be moved here", autocomplete=True, required=True),
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
                f"Transfer group and group to delete are the same! Use '{self.delete_roles_option}' option to delete roles with a group")

        group_roles = BotRole.find({"group.$id": group.id})
        if transfer_group:
            # await group_roles.update(Set({BotManagedRole.group: transfer_group}))  # TODO wait for odmantic fix
            await group_roles.update(Set({"group.$id": transfer_group.id}))
        else:
            await group_roles.delete()

        group_name = utils.convert_to_name(group.name)
        await group.delete()
        roles_action_text = f"moved to '{utils.convert_to_name(transfer_group.name)}'" if transfer_group else "deleted"
        await ctx.send(f"Group '{group_name}' was deleted, roles from '{group_name}' were {roles_action_text}")

    # Status: done, tested
    @group_delete.autocomplete("group")
    async def _group_delete_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # Status: done, tested
    @group_delete.autocomplete("transfer_group")
    async def _group_delete_transfer_group(self, ctx: AutocompleteContext, transfer_group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, transfer_group, additional_options=[self.delete_roles_option])

    # Status: done, tested
    @subcommand(base="manage", subcommand_group="groups", name="edit_group")
    async def group_edit(
        self,
        ctx: InteractionContext,
        group: slash_str_option("Group to edit", autocomplete=True, required=True),
        priority: slash_int_option("Priority of the group (affects ordering)", required=False) = None,
        name: slash_str_option("Name of the group", required=False) = None,
        color: slash_str_option("Color theme of the group", autocomplete=True, required=False) = None,
        exclusive_roles: slash_bool_option("Should the person be allowed to have only one role from this group", required=False) = None,
        description: slash_str_option("Description of this group. Use 'None' to delete the description") = None
    ):
        """Edits parameters of the existing group"""
        await ctx.defer(ephemeral=True)

        group = await self.role_group_find(group_name=group, guild=ctx.guild, use_fuzzy_search=False)
        old_name = utils.convert_to_name(group.name)

        if priority:  # TODO: ensure that there is no duplicates in priority
            group.priority = priority
        if name:
            name = utils.convert_to_db_name(name)
            await self.validate_new_group_name(group_name=name, guild=ctx.guild)
            group.name = name
        if color:
            self.validate_color(color)
            group.color = color
        if exclusive_roles:
            group.exclusive_roles = exclusive_roles
        if description:
            if description == "None":
                description = ""
            group.description = description

        await group.save()
        await ctx.send(f"Group {old_name} was successfully updated")

    # Status: done, tested
    @group_edit.autocomplete("group")
    async def _group_edit_group(self, ctx: AutocompleteContext, group: str, **kwargs):
        return await self.role_group_autocomplete(ctx, group)

    # Status: done, tested
    @group_edit.autocomplete("color")
    async def _group_edit_color(self, ctx: AutocompleteContext, color: str, **kwargs):
        return await utils.color_autocomplete(ctx, color)

    # Status: not done
    @subcommand(base="role", subcommand_group="group", name="edit_roles", description="11")
    async def group_edit_roles(self, ctx: InteractionContext):
        pass

    # status: done, not tested (roles amount limitation?)
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
        if role.bot_managed or role.premium_subscriber or role.integration or role.default:
            logger.info(f"Skipping role '{role}' as it's system role")
            raise utils.BadBotArgument(f"Role {role.mention} is a system role and won't be managed")

        if not utils.can_manage_role(ctx.guild.me, role):
            logger.info(f"Skipping role '{role}' as bot cannot manage it")
            raise utils.BadBotArgument(f"Bot cannot manage role {role.mention}")

        if await BotRole.find_one(BotRole.role_id == role.id):
            logger.info(f"Skipping role '{role}' as it already exists")
            raise utils.BadBotArgument(f"Role {role.mention} already managed by bot")

        if emoji and not utils.is_emoji(emoji):
            raise utils.BadBotArgument(f"'{emoji}' is not a valid emoji")

        if not group:
            # when no group specified, try to find DEFAULT group for this server or create it
            group = await RoleGroup.find_one(
                RoleGroup.name == utils.convert_to_db_name(self.bot.config.default_manage_group),
                RoleGroup.guild_id == ctx.guild_id,
            )
            if not group:
                group = await self.create_new_role_group(guild=ctx.guild,
                                                         name=utils.convert_to_db_name(self.bot.config.default_manage_group),
                                                         description=self.bot.config.default_manage_group_desc)

        # Check that we can add more roles to the group, if that's not a default group
        if group.name != utils.convert_to_db_name(self.bot.config.default_manage_group):
            existing_roles_num = await BotRole.find({"group.$id": group.id}).count()
            if existing_roles_num >= self.bot.config.max_roles_in_group:
                raise utils.BadBotArgument(
                    f"Group {utils.convert_to_name(group.name)} already has too much roles! "
                    f"One group can't have more than {self.bot.config.max_roles_in_group} roles")

        db_role = BotRole(
            role_id=role.id,
            group=group,
            name=role.name,
            description=description,
            emoji=emoji,
            assignable=assignable,
        )
        await db_role.insert()

    # status: done, tested
    @staticmethod
    async def stop_role_tracking(role_id: int):
        """Removes role from internal database by ID"""
        db_role = await BotRole.find_one(BotRole.role_id == role_id)
        if db_role:
            await db_role.delete()
        else:
            raise utils.BadBotArgument(f"Role with ID {role_id} is not managed by bot")

    # status: done, tested
    @staticmethod
    async def create_new_role_group(guild: dis_snek.Guild, name: str, color: Optional[str] = None,
                                    exclusive_roles: bool = False, description: str = ""):
        """Creates a new role group for the guild, checks for collisions"""
        name = utils.convert_to_db_name(name)
        await RoleSelector.validate_new_group_name(group_name=name, guild=guild)

        priority = await RoleGroup.find(RoleGroup.guild_id == guild.id).max("priority") or -1
        priority = priority + 1
        group = RoleGroup(guild_id=guild.id,
                          priority=priority,
                          name=name,
                          color=color,
                          exclusive_roles=exclusive_roles,
                          description=description)
        await group.insert()
        return group

    # status: done, tested
    @staticmethod
    async def role_group_find(group_name: str, guild: dis_snek.Guild, use_fuzzy_search: bool = True) -> RoleGroup:
        """Method to get RoleGroup object from data received from group slash options"""
        # try and get role group with exact same name
        group_name = utils.convert_to_db_name(group_name)
        group = await RoleGroup.find_one(RoleGroup.name == group_name, RoleGroup.guild_id == guild.id)
        if group is None:  # user gave us incorrect or incomplete role name
            if not use_fuzzy_search:
                raise utils.BadBotArgument(f"Can't find a group '{group_name}' for this server!")

            groups = await RoleGroup.find(RoleGroup.guild_id == guild.id).to_list()
            groups_list = [g.name for g in groups]
            fuzzy_group_name = fuzzy_find(group_name, groups_list)
            if fuzzy_group_name is None:
                raise utils.BadBotArgument(f"Can't find a group '{group_name}' for this server!")

            group = await RoleGroup.find_one(RoleGroup.name == group_name, RoleGroup.guild_id == guild.id)

        return group

    # status: done, tested, need permissions, reason in add_role doesn't work, should be fixed on update
    @staticmethod
    async def try_assign_role(requester: dis_snek.Member, target: dis_snek.Member, role: dis_snek.Role, reason: Absent[str] = MISSING):
        print(BotRole.role_id)
        db_role = await BotRole.find_one(BotRole.role_id == role.id)
        if not db_role:
            raise utils.BadBotArgument(f"Trying to assign role {role.mention}, that is not in the database! Probably you shouldn't do it")

        # TODO EXTRACT THIS CODE
        # Making sure we're not increasing permissions over what's available for requester with this assignment
        if not db_role.assignable:
            # TODO: permissions!
            if requester.top_role and requester.top_role > role and requester.has_permission(dis_snek.Permissions.MANAGE_ROLES):
                pass
            else:
                raise utils.BadBotArgument("Trying to assign role without a permissions to do it!")

        # remove conflicting roles
        group = await db_role.group.fetch()
        if group.exclusive_roles:
            target_roles = {target_role.id: target_role for target_role in target.roles}
            db_roles = BotRole.find({"group.$id": group.id}, In(BotRole.role_id, list(target_roles.keys())))
            async for db_role in db_roles:
                await target.remove_role(target_roles[db_role.role_id],
                                         reason=f"Removing a conflicting role from group {utils.convert_to_name(group.name)} "
                                                f"when assigning '{role.name}' by {requester.display_name}")

        await target.add_role(role, reason=reason)

    # Status: done, tested
    @staticmethod
    async def try_remove_role(requester: dis_snek.Member, target: dis_snek.Member, role: dis_snek.Role, reason: Absent[str] = MISSING):
        db_role = await BotRole.find_one(BotRole.role_id == role.id)
        if not db_role:
            raise utils.BadBotArgument(f"Trying to remove role {role.mention}, that is not in the database! Probably you shouldn't do it")

        # TODO EXTRACT THIS CODE
        # Making sure we're not increasing permissions over what's available for requester with this assignment
        if not db_role.assignable:
            # TODO: permissions!
            if requester.top_role and requester.top_role > role and requester.has_permission(dis_snek.Permissions.MANAGE_ROLES):
                pass
            else:
                raise utils.BadBotArgument("Trying to unassign role without a permissions to do it!")

        if not db_role.assignable:
            # TODO: permissions!
            pass

        await target.remove_role(role, reason=reason)

    # status: done, tested
    @staticmethod
    async def validate_new_group_name(group_name: str, guild: dis_snek.Guild):
        if ' ' in group_name:
            raise utils.BadBotArgument(f"Group name shouldn't contain spaces! "
                                       f"They should be converted to _ automatically, but something went wrong")
        if await RoleGroup.find_one(RoleGroup.name == group_name, RoleGroup.guild_id == guild.id):
            raise utils.BadBotArgument(f"Role group with name '{group_name}' already exists on this server!")

    # status: done, tested
    @staticmethod
    def validate_color(color: str):
        """Removes color description from the incorrect client autocomplete logic and checks, if the color is valid"""
        if not utils.is_hex(color):
            raise utils.BadBotArgument(f"'{color}' is not a hex color! Put hex color or use auto-complete results")

    # status: done, tested
    @staticmethod
    async def role_group_autocomplete(ctx: AutocompleteContext, group: str, additional_options: List[str] = None):
        """Autocompletes role groups request with optional additional options"""
        groups = await RoleGroup.find(RoleGroup.guild_id == ctx.guild_id).to_list()
        groups_list = []
        if additional_options:
            groups_list.extend(additional_options)
        groups_list.extend(g.name for g in groups)

        results = fuzzy_autocomplete(group, groups_list)
        results = [value[0] for value in results]
        await ctx.send(results)


def setup(bot):
    RoleSelector(bot)
    bot.add_model(RoleGroup)
    bot.add_model(BotRole)
    bot.add_model(RoleSelectorMessage)
