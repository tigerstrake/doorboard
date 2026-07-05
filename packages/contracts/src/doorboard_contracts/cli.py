from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Literal, TypeAliasType, Union, get_args, get_origin
from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from doorboard_contracts.events import (
    EVENT_MODELS,
    EVENT_TYPE_TO_MODEL,
    BaseEvent,
    ErrorDetail,
    ErrorEnvelope,
    HealthPayload,
    PresenceLabel,
    SessionState,
    event_json_schema,
)
from doorboard_contracts.examples import example_events

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGE_ROOT.parents[1]
SCHEMA_DIR = PACKAGE_ROOT / "schemas"
TS_TYPES_PATH = PACKAGE_ROOT / "types" / "index.ts"
FIXTURE_DIR = REPO_ROOT / "tools" / "seed-data" / "events"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_schemas(output_dir: Path = SCHEMA_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "doorboard-event.schema.json", event_json_schema())
    _write_json(output_dir / "error-envelope.schema.json", ErrorEnvelope.model_json_schema())
    _write_json(output_dir / "health-payload.schema.json", HealthPayload.model_json_schema())
    for event_model in EVENT_MODELS:
        event_type = _event_type(event_model)
        filename = event_type.replace(".", "-") + ".schema.json"
        _write_json(output_dir / filename, event_model.model_json_schema())


def export_fixtures(output_dir: Path = FIXTURE_DIR) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for event in example_events():
        filename = event.type.replace(".", "-") + ".json"
        _write_json(output_dir / filename, event.model_dump(mode="json"))


def generate_ts(output_path: Path = TS_TYPES_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_render_ts(), encoding="utf-8")


def _event_type(model: type[BaseEvent]) -> str:
    annotation = model.model_fields["type"].annotation
    args = get_args(annotation)
    if len(args) != 1 or not isinstance(args[0], str):
        msg = f"{model.__name__}.type must be a single string Literal"
        raise TypeError(msg)
    return args[0]


def _strip_annotated(annotation: Any) -> Any:
    if isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    if get_origin(annotation) is Annotated:
        return get_args(annotation)[0]
    return annotation


def _is_optional(annotation: Any) -> bool:
    annotation = _strip_annotated(annotation)
    return get_origin(annotation) in (Union, UnionType) and type(None) in get_args(annotation)


def _ts_type(annotation: Any) -> str:
    annotation = _strip_annotated(annotation)
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, UnionType):
        return " | ".join(_ts_type(arg) for arg in args if arg is not type(None)) + (
            " | null" if type(None) in args else ""
        )
    if origin is Literal:
        return " | ".join(json.dumps(arg) for arg in args)
    if origin is list:
        return f"Array<{_ts_type(args[0])}>"
    if annotation in (str, UUID, datetime, date, AwareDatetime):
        return "string"
    if annotation is int or annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return annotation.__name__
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.__name__
    msg = f"unsupported TypeScript annotation: {annotation!r}"
    raise TypeError(msg)


def _render_model_interface(model: type[BaseModel]) -> list[str]:
    lines = [f"export interface {model.__name__} {{"]
    for name, field in model.model_fields.items():
        optional = "?" if not field.is_required() and _is_optional(field.annotation) else ""
        lines.append(f"  {name}{optional}: {_ts_type(field.annotation)};")
    lines.append("}")
    return lines


def _payload_models() -> list[type[BaseModel]]:
    seen: set[type[BaseModel]] = set()
    ordered: list[type[BaseModel]] = []

    def visit_model(model: type[BaseModel]) -> None:
        if model in seen:
            return
        seen.add(model)
        for field in model.model_fields.values():
            visit_annotation(field.annotation)
        ordered.append(model)

    def visit_annotation(annotation: Any) -> None:
        annotation = _strip_annotated(annotation)
        origin = get_origin(annotation)
        if origin in (Union, UnionType, list, Literal):
            for arg in get_args(annotation):
                visit_annotation(arg)
            return
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            visit_model(annotation)

    for event_model in EVENT_MODELS:
        payload_annotation = event_model.model_fields["payload"].annotation
        if isinstance(payload_annotation, type) and issubclass(payload_annotation, BaseModel):
            visit_model(payload_annotation)
    return ordered


def _render_event_interface(model: type[BaseEvent]) -> list[str]:
    event_type = _event_type(model)
    payload = model.model_fields["payload"].annotation
    if not isinstance(payload, type) or not issubclass(payload, BaseModel):
        msg = f"{model.__name__}.payload must be a Pydantic model"
        raise TypeError(msg)
    return [
        f"export interface {model.__name__} {{",
        "  event_id: string;",
        f"  type: {json.dumps(event_type)};",
        "  source: string;",
        "  occurred_at: string;",
        "  monotonic_ms: number;",
        "  door_id: string;",
        "  trace_id: string;",
        f"  payload: {payload.__name__};",
        "}",
    ]


def _render_enum(name: str, values: Iterable[str]) -> list[str]:
    value_list = " | ".join(json.dumps(value) for value in values)
    return [f"export type {name} = {value_list};"]


def _render_ts() -> str:
    sections: list[str] = [
        "// Generated by `contracts generate-ts`. Do not edit by hand.",
        "",
        *_render_enum("PresenceLabel", [item.value for item in PresenceLabel]),
        "",
        *_render_enum("SessionState", [item.value for item in SessionState]),
        "",
        *_render_model_interface(ErrorDetail),
        "",
        *_render_model_interface(ErrorEnvelope),
        "",
        'export type HealthStatus = "ok" | "degraded" | "down";',
        "",
        *_render_model_interface(HealthPayload),
        "",
    ]
    for model in _payload_models():
        sections.extend(_render_model_interface(model))
        sections.append("")
    event_names: list[str] = []
    for event_model in EVENT_MODELS:
        event_names.append(event_model.__name__)
        sections.extend(_render_event_interface(event_model))
        sections.append("")
    sections.append(f"export type DoorboardEvent = {' | '.join(event_names)};")
    sections.append("")
    sections.append(
        "export type DoorboardEventType = "
        + " | ".join(json.dumps(event_type) for event_type in EVENT_TYPE_TO_MODEL)
        + ";"
    )
    sections.append("")
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(prog="contracts")
    subcommands = parser.add_subparsers(dest="command", required=True)

    export_schemas_parser = subcommands.add_parser("export-schemas")
    export_schemas_parser.add_argument("--output-dir", type=Path, default=SCHEMA_DIR)

    generate_ts_parser = subcommands.add_parser("generate-ts")
    generate_ts_parser.add_argument("--output", type=Path, default=TS_TYPES_PATH)

    export_fixtures_parser = subcommands.add_parser("export-fixtures")
    export_fixtures_parser.add_argument("--output-dir", type=Path, default=FIXTURE_DIR)

    args = parser.parse_args()
    if args.command == "export-schemas":
        export_schemas(args.output_dir)
    elif args.command == "generate-ts":
        generate_ts(args.output)
    elif args.command == "export-fixtures":
        export_fixtures(args.output_dir)


if __name__ == "__main__":
    main()
