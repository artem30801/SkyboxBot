import asyncio
from typing import Any, Callable, Optional, Type, Union
from distutils import util as convert_utils

import attr
import dis_snek
from dis_snek import Context, Modal, ParagraphText, ShortText
from pydantic.fields import ModelField
from beanie import Link

from utils import misc as utils
from utils.db import Document, generic_edit


@attr.define()
class EditorField:
    label: str = attr.field()
    type = attr.field()
    converter: Callable[[str], Any] = attr.field()
    required: bool = attr.field(default=True)
    value: Optional[str] = attr.field(default=None)  # Current value of the field
    placeholder: Optional[str] = attr.field(default=None)  # Help/instruction


@attr.define()
class ModelEditor:
    ctx: Context = attr.field()
    model: Type[Document] = attr.field()
    to_edit: list[Document] = attr.field(factory=list)

    fields: dict[str, EditorField] = attr.field(factory=dict, init=False)

    MIXED_VALUES = "<MIXED VALUES>"
    NONE_VALUE = "NONE"

    @property
    def is_bulk_edit(self):
        return len(self.to_edit) > 1

    async def generate_fields(self):
        for instance in self.to_edit:
            await instance.fetch_all_links()

        field: ModelField
        for field in self.model.__fields__.values():
            editor_field = self.process_field(field)
            if editor_field is None:
                continue
            self.fields[field.name] = editor_field

    # Field processing!

    def process_field(self, field: ModelField) -> Optional[EditorField]:
        if not field.field_info.extra.get("editable", False):
            return None

        type_ = self._field_type(field)
        converter = self._field_converter(field, type_)
        required = self._field_required(field, type_)
        value = self._field_value(field) if self.to_edit else None
        placeholder = self._field_placeholder(field, type_)

        modal_field = EditorField(
            label=field.name,
            type=type_,
            converter=converter,
            required=required,
            value=value,
            placeholder=placeholder,
        )

        return modal_field

    @staticmethod
    def _field_type(field: ModelField):
        """
        Getting actual type of the field
        """
        type_bases = [parent_type for parent_type in field.type_.__mro__[:-1]]
        if bool in type_bases:
            type_ = bool  # bc bool inherits from int
        else:
            type_ = type_bases[-1]
        return type_

    def _field_converter(self, field: ModelField, type_) -> Callable[[str], Any]:
        """
        Getting converter for the type
        """
        if type_ is bool:
            return self.bool_converter
        elif type_ is str:
            return self.str_converter

        return type_

    @staticmethod
    def _field_required(field: ModelField, type_) -> bool:
        """
        Determine if the filed is "required"
        """
        required = not field.allow_none
        if type_ is str:
            required = required and field.default != ""  # if field.default is emtpy str - allow "not required"

        return required

    def _field_value(self, field: ModelField) -> Optional[str]:
        """
        If we are editing existing instance - prefill fields with existing values
        """
        values = {self._get_field_value(instance, field.name) for instance in self.to_edit}
        prefill = self.MIXED_VALUES if len(values) > 1 else str(values.pop())
        return prefill

    def _get_field_value(self, obj: Any, field: str) -> Any:
        value = getattr(obj, field)
        # None returns empty string, but we want "None" there
        if value is None:
            return self.NONE_VALUE
        return value

    @staticmethod
    def _field_placeholder(field: ModelField, type_) -> Optional[str]:
        """
        Getting help messages/placeholder for the type
        """
        # TODO placeholders help
        if type_ is str:
            placeholder = f"Text"
        elif type_ is bool:
            placeholder = f"True/False"
        else:
            placeholder = None

        return placeholder

    # Convertors
    @classmethod
    def str_converter(cls, to_convert: str):
        if to_convert.casefold() == cls.NONE_VALUE.casefold():
            return None
        return to_convert

    @staticmethod
    def bool_converter(to_convert: str):
        # Because strtobool returns int
        return bool(convert_utils.strtobool(to_convert))


@attr.define()
class ModalModelEditor(ModelEditor):
    async def send_modal(
            self,
            title: Optional[str] = None,
            instruction: Optional[str] = None,
            ephemeral_response: bool = False,
    ):
        await self.generate_fields()
        modals = self.generate_modals(title, instruction)
        assert modals
        responses = []
        for modal in modals:
            send_target = responses[-1] if responses else self.ctx
            await send_target.send_modal(modal)
            response = await self.ctx.bot.wait_for_modal(modal)
            responses.append(response)

        response_message = await responses[-1].send("Processing...", ephemeral=ephemeral_response)
        response_data = await self.process_modal_response(responses)

        if self.to_edit:
            edited = []
            for instance in self.to_edit:
                diff = await generic_edit(instance, response_data)
                if diff:
                    edited.append(edited)

            await response_message.edit("edited")
        else:
            pass

        return response_data, response_message

    def generate_modals(
            self,
            title: Optional[str] = None,
            instruction: Optional[str] = None,
    ):
        title = title or self._get_modal_title()

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

        assert self.fields

        for field_name, data in self.fields.items():
            component = ShortText(
                custom_id=field_name,
                label=data.label,
                required=data.required,
                value=data.value or dis_snek.MISSING,
                placeholder=data.placeholder or dis_snek.MISSING,
            )
            components.append(component)

        blocks = [components[i:i+5] for i in range(0, len(components), 5)]

        modals = []
        for idx, block in enumerate(blocks):
            modals.append(
                Modal(
                    title=title if len(blocks) == 1 else f"{title} {idx+1}/{len(blocks)}",
                    components=block,
                )
            )

        return modals

    def _get_modal_title(self):
        if self.to_edit:
            operation = "Bulk edit" if self.is_bulk_edit else "Edit"
        else:
            operation = "New"
        title = f"{operation} {self.model.__name__}{'s' if self.is_bulk_edit else ''}"
        return title

    async def process_modal_response(self, responses: [dis_snek.ModalContext]) -> dict[str, Any]:
        responses_dict = dict()
        for response in responses:
            responses_dict.update(response.kwargs)

        results = dict()
        for field_name, response_value in responses_dict.items():
            if response_value == self.MIXED_VALUES:
                continue

            field = self.fields[field_name]

            converter = field.converter
            converted = converter(response_value)
            if asyncio.iscoroutine(converted):
                converted = await converted

            if converted == field.value:
                # Ignore response value when it matches current value of the field
                continue

            results[field_name] = converted

        return results

# async def edit_documents_with_modal(
#         ctx: Context,
#         to_edit: Union[Document, list[Document]],
#         title: Optional[str] = None,
#         instruction: Optional[str] = None,
#         response_is_ephemeral: bool = True,
# ) -> (Union[Document, list[Document]], dis_snek.Message):
#     to_edit_list = to_edit if isinstance(to_edit, list) else [to_edit]
#     model = type(to_edit_list[0])
#     modal_results, response_message = await get_document_modal_results(
#         ctx,
#         model,
#         title,
#         instruction,
#         to_edit_list,
#         response_is_ephemeral,
#     )
#     # TODO: Deal with validation errors. Here? Better not
#     edited = []
#     # for doc in to_edit_list:
#     #     diff = await generic_edit(doc, modal_results)
#     #     if diff:
#     #         edited.append(doc)
#     return edited, response_message
#
#
# async def get_document_modal_results(
#         ctx: Context,
#         doc_type: Type[Document],
#         title: Optional[str] = None,
#         instruction: Optional[str] = None,
#         to_edit: Optional[Union[Document, list[Document]]] = None,
#         response_is_ephemeral: bool = True,
# ) -> (dict[str, Any], dis_snek.Message):
#     if to_edit and not isinstance(to_edit, list):
#         to_edit = [to_edit]
#
#     is_bulk_edit = len(to_edit) > 1
#
#     fields = dict()
#     field: ModelField
#     for field_name, field in doc_type.__fields__.items():
#         if "editable" not in field.field_info.extra:
#             continue
#         if not field.field_info.extra["editable"]:
#             continue
#
#         # Getting actual type of the field
#         type_bases = [parent_type for parent_type in field.type_.__mro__[:-1]]
#         if bool in type_bases:
#             type_ = bool  # bc bool inherits from int
#         else:
#             type_ = type_bases[-1]
#
#         # Getting converter for the type
#         if type_ is bool:
#             converter = modal_bool_converter
#         elif type_ is str:
#             converter = modal_str_converter
#         else:
#             converter = type_
#
#         # Getting help messages/placeholder for the type
#         # TODO placeholders help
#         if type_ is str:
#             placeholder = f"Text"
#         elif type_ is bool:
#             placeholder = f"True/False"
#         else:
#             placeholder = None
#
#         # If we are editing existing instance - prefill fields with existing values
#         if to_edit:
#             if is_bulk_edit:
#                 # values = {getattr(instance, field_name) for instance in edit_instance}
#                 values = {await get_field_value(instance, field_name) for instance in to_edit}
#                 prefill = MIXED_VALUES if len(values) > 1 else str(values.pop())
#             else:
#                 value = await get_field_value(to_edit, field_name)
#                 prefill = str(value) if value else None
#         else:
#             prefill = None
#
#         # Determine if the filed is "required"
#         required = not field.allow_none
#         if type_ is str:
#             required = required and field.default != ""  # if field.default is emtpy str - allow "not required"
#
#         modal_field = EditorField(
#             converter=converter,
#             label=field_name,
#             required=required,
#             value=prefill,
#             placeholder=placeholder,
#         )
#
#         fields[field_name] = modal_field
#
#     if not title:
#         if to_edit:
#             operation = "Bulk edit" if is_bulk_edit else "Edit"
#         else:
#             operation = "New"
#         title = f"{operation} {doc_type.__name__}{'s' if is_bulk_edit else ''}"
#
#     return await get_modal_result(
#         ctx,
#         title,
#         fields,
#         instruction,
#         response_is_ephemeral,
#     )
#
#
# async def get_modal_result(
#         ctx: Context,
#         title: str,
#         fields: dict[str, "EditorField"],
#         instruction: Optional[str] = None,
#         response_is_ephemeral: bool = True,
# ) -> (dict[str, Any], dis_snek.Message):
#     components = []
#     if instruction:
#         components.append(
#             ShortText(
#                 custom_id="instruction",
#                 label="Instruction",
#                 value=instruction,
#                 required=False,
#             )
#         )
#
#     for field_name, data in fields.items():
#         component = ShortText(
#             custom_id=field_name,
#             label=data.label,
#             required=data.required,
#             value=data.value or dis_snek.MISSING,
#             placeholder=data.placeholder or dis_snek.MISSING,
#         )
#         components.append(component)
#
#     groups = [components]
#
#     if len(components) > 5:
#         raise Exception("More than 5 fields are not supported for now")
#     # TODO: handle a case, when we have more than 5 components
#     # chain modals?
#     modal = Modal(title=title, components=components)
#     await ctx.send_modal(modal)
#
#     response = await ctx.bot.wait_for_modal(modal)
#     response_message = await response.send("Processing...", ephemeral=response_is_ephemeral)
#     results = dict()
#     for field_name, response_value in response.kwargs.items():
#         # TODO: do we have any cases where we want to actually write this value? I think not
#         if response_value == MIXED_VALUES:
#             continue
#         field = fields[field_name]
#         converter = field.converter
#         converted = converter(response_value)
#         if asyncio.iscoroutine(converted):
#             converted = await converted
#         results[field_name] = converted
#
#     return results, response_message
