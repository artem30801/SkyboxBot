import asyncio
from typing import Any, Callable, Optional, Type, Union

import attr
import dis_snek
from dis_snek import Context, Modal, ParagraphText, ShortText
from pydantic.fields import ModelField
from beanie import Link

from utils import misc as utils
from utils.db import Document


@attr.define()
class ModalField:
    converter: Callable = attr.field()
    label: str = attr.field()
    required: bool = attr.field(default=True)
    value: Optional[str] = attr.field(default=None)
    placeholder: Optional[str] = attr.field(default=None)


MIXED_VALUES = "<MIXED VALUES>"


async def get_field_value(obj, field):
    value = getattr(obj, field)
    if isinstance(value, Link):
        value = await value.fetch()

    return value


async def generate_modal(
        ctx: Context,
        model: Type[Document],
        title: Optional[str] = None,
        instruction: Optional[str] = None,
        edit_instance: Optional[Union[Document, list[Document]]] = None,
) -> dict[str, any]:
    # from scales.roles import BotRole

    if edit_instance and not isinstance(edit_instance, list):
        edit_instance = [edit_instance]

    bulk_edit = len(edit_instance) > 1

    fields = dict()
    field: ModelField
    for field_name, field in model.__fields__.items():
        if field_name in {"id", "revision_id"}:
            continue

        if not field.field_info.extra.get("editable", True):
            continue

        # Getting actual type of the field
        type_bases = [parent_type for parent_type in field.type_.__mro__[:-1]]
        if bool in type_bases:
            type_ = bool  # bc bool inherits from int
        else:
            type_ = type_bases[-1]

        # Getting converter for the type
        match type_:
            case any_:
                converter = any_

        # Getting help messages/placeholder for the type
        # TODO placeholders help
        if type_ is str:
            placeholder = f"Text"
        else:
            placeholder = None

        # If we are editing existing instance - prefill fields with existing values
        if edit_instance:
            if bulk_edit:
                # values = {getattr(instance, field_name) for instance in edit_instance}
                values = {await get_field_value(instance, field_name) for instance in edit_instance}
                prefill = MIXED_VALUES if len(values) > 1 else values.pop()
            else:
                value = await get_field_value(edit_instance, field_name)
                prefill = str(value) if value else None
        else:
            prefill = None

        # Determine if the filed is "required"
        required = not field.allow_none
        if type_ is str:
            required = required and field.default != ""  # if field.default is emtpy str - allow "not required"

        modal_field = ModalField(converter=converter,
                                 label=field_name,
                                 required=required,
                                 value=prefill,
                                 placeholder=placeholder,
                                 )

        fields[field_name] = modal_field

    if not title:
        if edit_instance:
            operation = "Bulk edit" if bulk_edit else "Edit"
        else:
            operation = "New"
        title = f"{operation} {model.__name__}{'s' if bulk_edit else ''}"

    return await get_modal_result(ctx,
                                  title,
                                  fields,
                                  instruction,
                                  )


def check_emoji(emoji: str):
    if not utils.is_emoji(emoji):
        raise utils.BadBotArgument(f"'{emoji}' is not a valid emoji")


async def get_modal_result(ctx: Context,
                           title: str,
                           fields: dict[str, "ModalField"],
                           instruction: Optional[str] = None,
                           ) -> dict[str, Any]:
    components = []
    if instruction:
        components.append(
            ShortText(
                custom_id="instruction",
                label="Instruction",
                value=instruction,
                required=False,
            )
        )

    for field_name, data in fields.items():
        component = ShortText(
            custom_id=field_name,
            label=data.label,
            required=data.required,
            value=data.value or dis_snek.MISSING,
            placeholder=data.placeholder or dis_snek.MISSING,
        )
        components.append(component)

    if len(components) > 5:
        raise Exception("More than 5 fields are not supported for now")
    # TODO: handle a case, when we have more than 5 components
    modal = Modal(title=title, components=components)
    await ctx.send_modal(modal)

    response = await ctx.bot.wait_for_modal(modal)
    results = dict()
    for field_name, response_value in response.values():
        field = fields[field_name]
        converter = field.converter
        converted = converter(response_value)  # todo async support
        results[field_name] = converted

    print(results)
    return results
