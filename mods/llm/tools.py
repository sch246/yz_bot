"""Conversion of annotated Python functions into model-callable tools."""

from __future__ import annotations

from collections.abc import Callable
import inspect
import re
from types import UnionType
from typing import get_args, get_origin, get_type_hints, Union


def _json_type(annotation) -> dict:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        values = [value for value in get_args(annotation) if value is not type(None)]
        return _json_type(values[0]) if len(values) == 1 else {"anyOf": [_json_type(value) for value in values]}
    if origin in (list, tuple, set):
        args = get_args(annotation)
        return {"type": "array", "items": _json_type(args[0]) if args else {}}
    if origin is dict:
        return {"type": "object"}
    return {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        list: {"type": "array"},
        dict: {"type": "object"},
    }.get(annotation, {"type": "string"})


class Tool:
    """Expose a normal annotated function as an OpenAI function tool."""

    def __init__(self, call: Callable, name: str | None = None) -> None:
        self.call = call
        self.description = self._load(name or call.__name__)

    def _load(self, name: str) -> dict:
        parameters = inspect.signature(self.call).parameters
        try:
            hints = get_type_hints(self.call)
        except Exception:
            hints = {}
        lines = [line.strip() for line in (inspect.getdoc(self.call) or "").splitlines() if line.strip()]
        description_lines: list[str] = []
        parameter_docs: dict[str, str] = {}
        in_parameters = False
        for line in lines:
            if line in ("@param", "Args:"):
                in_parameters = True
                continue
            if line == "Returns:":
                in_parameters = False
                continue
            if in_parameters and ":" in line:
                parameter, detail = line.split(":", 1)
                if parameter.strip() in parameters:
                    parameter_docs[parameter.strip()] = detail.strip()
                    continue
            if not in_parameters:
                description_lines.append(line)
        properties = {}
        for parameter, value in parameters.items():
            schema = _json_type(hints.get(parameter, value.annotation))
            detail = parameter_docs.get(parameter, "")
            if detail:
                schema["description"] = detail
                enum = re.search(r"enum:\s*\[([^]]+)]", detail)
                if enum:
                    schema["enum"] = [item.strip(" \"'") for item in enum.group(1).split(",")]
            properties[parameter] = schema
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": "\n".join(description_lines),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": [name for name, value in parameters.items() if value.default is inspect.Parameter.empty],
                },
            },
        }
