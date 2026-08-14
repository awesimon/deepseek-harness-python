"""Prompt and Tool contributions selected through Agent Scope layers."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator

from .scope import AgentScope, LayeredRegistry
from .values import JsonValue, Message, ModelToolDefinition, freeze_json_object, thaw_json


@dataclass(frozen=True, slots=True)
class PromptRenderContext:
    """Immutable inputs available while rendering one Prompt Section."""

    scope: AgentScope
    history: tuple[Message, ...]


type PromptRenderer = Callable[[PromptRenderContext], str | Awaitable[str]]


@dataclass(frozen=True, slots=True)
class PromptSection:
    """One named System Prompt contribution."""

    name: str
    order: int
    render: PromptRenderer

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("prompt section name must not be empty")


class PromptRegistry:
    """Layered Prompt Sections with immutable Step rendering."""

    def __init__(self) -> None:
        self._entries = LayeredRegistry[PromptSection]("prompt section")

    def register(
        self,
        section: PromptSection,
        *,
        scope: AgentScope | None = None,
    ) -> Callable[[], None]:
        """Register a Prompt Section and return its disposer."""
        return self._entries.register(section.name, section, scope=scope)

    async def render(self, scope: AgentScope, history: tuple[Message, ...]) -> str:
        """Render the current Scope snapshot in stable order."""
        sections = sorted(
            self._entries.snapshot(scope).values(),
            key=lambda section: (section.order, section.name),
        )
        context = PromptRenderContext(scope, history)
        rendered: list[str] = []
        for section in sections:
            value = section.render(context)
            if inspect.isawaitable(value):
                value = await value
            if value:
                rendered.append(value)
        return "\n\n".join(rendered)


type ToolHandler = Callable[[Mapping[str, JsonValue]], JsonValue | Awaitable[JsonValue]]


@dataclass(frozen=True, slots=True)
class Tool:
    """One model-visible Tool and its backend handler."""

    name: str
    description: str
    parameters: Mapping[str, JsonValue]
    handler: ToolHandler

    def __post_init__(self) -> None:
        parameters = freeze_json_object(self.parameters)
        # jsonschema's public Schema alias uses Any for recursively open keyword values.
        schema = cast(Mapping[str, Any], thaw_json(parameters))
        Draft202012Validator.check_schema(schema)
        object.__setattr__(self, "parameters", parameters)
        ModelToolDefinition(self.name, self.description, parameters)

    @property
    def model_definition(self) -> ModelToolDefinition:
        """Return the handler-free definition captured in model requests."""
        return ModelToolDefinition(self.name, self.description, self.parameters)

    def validate(self, arguments: Mapping[str, JsonValue]) -> None:
        """Validate one invocation against this Tool's JSON Schema."""
        schema = cast(Mapping[str, Any], thaw_json(self.parameters))
        validator = Draft202012Validator(schema)
        # jsonschema exposes validate through an incompletely typed validator protocol.
        validator.validate(thaw_json(arguments))  # pyright: ignore[reportUnknownMemberType]


class ToolRegistry:
    """Layered Tools captured atomically for each Agent Step."""

    def __init__(self) -> None:
        self._entries = LayeredRegistry[Tool]("tool")

    def register(
        self,
        tool: Tool,
        *,
        scope: AgentScope | None = None,
    ) -> Callable[[], None]:
        """Register a Tool and return its disposer."""
        return self._entries.register(tool.name, tool, scope=scope)

    def snapshot(self, scope: AgentScope) -> Mapping[str, Tool]:
        """Return the immutable handler snapshot for one Step."""
        return self._entries.snapshot(scope)
