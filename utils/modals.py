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
    edited: list[Document] = attr.field(factory=list, init=False)

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
        # elif type_ is str:
        #     return self.str_converter

        if field.allow_none:
            pass

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
        values = {str(self._get_field_value(instance, field.name)) for instance in self.to_edit}
        prefill = self.MIXED_VALUES if len(values) > 1 else values.pop()
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
    # @classmethod
    # def str_converter(cls, to_convert: str):
    #     if to_convert.casefold() == cls.NONE_VALUE.casefold():
    #         return None
    #     return to_convert

    @classmethod
    def none_converter(cls, to_convert: str):
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
        send_target = self.ctx
        for i, modal in enumerate(modals, 1):
            await send_target.send_modal(modal)
            response = await self.ctx.bot.wait_for_modal(modal)
            responses.append(response)

            if i < len(modals):
                btn = dis_snek.Button(
                    style=dis_snek.ButtonStyles.BLUE,
                    label="Next",
                    emoji="⏭️",
                )
                msg = await response.send(
                    f"Click to proceed to the next page of the modal (`{i+1}/{len(modals)}`)",
                    components=btn,
                    ephemeral=True,
                )
                event = await self.ctx.bot.wait_for_component(components=btn, timeout=5*60)
                send_target = event.context

        response_message = await responses[-1].send("Processing...", ephemeral=ephemeral_response)
        response_data = await self.process_modal_response(responses)

        if self.to_edit:
            for instance in self.to_edit:
                diff = await generic_edit(instance, response_data)
                if diff:
                    self.edited.append(instance)
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

        blocks = [components[i:i + 5] for i in range(0, len(components), 5)]

        modals = []
        for idx, block in enumerate(blocks):
            modals.append(
                Modal(
                    title=title if len(blocks) == 1 else f"{title} {idx + 1}/{len(blocks)}",
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

            if response_value == field.value:
                # Ignore response value when it matches current value of the field
                continue

            converter = field.converter
            converted = converter(response_value)
            if asyncio.iscoroutine(converted):
                converted = await converted

            results[field_name] = converted

        return results
