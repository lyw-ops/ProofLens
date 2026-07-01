from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DECLARATION_MODIFIERS = {"private", "protected", "noncomputable", "unsafe", "partial"}
DECLARATION_KEYWORDS = {"theorem", "lemma", "def", "abbrev", "example", "axiom", "constant", "opaque"}


@dataclass
class FormalParameter:
    names: list[str]
    type: str
    binder: str = "explicit"
    role: str = "parameter"

    def to_dict(self) -> dict[str, Any]:
        return {
            "names": self.names,
            "type": self.type,
            "binder": self.binder,
            "role": self.role,
        }


@dataclass
class FormalType:
    parameters: list[FormalParameter]
    conclusion: str

    @property
    def assumptions(self) -> list[FormalParameter]:
        return [
            parameter
            for parameter in self.parameters
            if parameter.role == "assumption"
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "assumptions": [parameter.to_dict() for parameter in self.assumptions],
            "conclusion": self.conclusion,
        }


def decompose_formal_type(text: str) -> FormalType:
    normalized = _normalize(text)
    if not normalized:
        return FormalType(parameters=[], conclusion="")
    if normalized.startswith("forall "):
        return _decompose_forall(normalized)
    if not _starts_with_declaration_keyword(normalized):
        return FormalType(parameters=[], conclusion=normalized)
    return _decompose_declaration_statement(normalized)


def normalize_formal_text(text: str) -> str:
    return _normalize(text)


def _decompose_forall(text: str) -> FormalType:
    rest = text[len("forall "):].strip()
    parameters: list[FormalParameter] = []
    while rest:
        rest = rest.strip()
        if rest.startswith(","):
            rest = rest[1:].strip()
            break
        if not rest or rest[0] not in "([{":
            break
        close = {"(": ")", "{": "}", "[": "]"}[rest[0]]
        content, next_index = _read_balanced(rest, 0, close)
        if content is None:
            break
        parameter = _parse_binder(content, _binder_name(rest[0]))
        if parameter:
            parameters.append(parameter)
        rest = rest[next_index:].strip()
    conclusion = rest[1:].strip() if rest.startswith(",") else rest.strip()
    return FormalType(parameters=parameters, conclusion=conclusion)


def _decompose_declaration_statement(text: str) -> FormalType:
    header = text.split(":=", 1)[0].strip()
    header = _drop_declaration_prefix(header)
    header = _drop_decl_name(header)
    parameters: list[FormalParameter] = []
    index = 0
    while index < len(header):
        while index < len(header) and header[index].isspace():
            index += 1
        if index >= len(header):
            break
        if header[index] == ":":
            conclusion = header[index + 1 :].strip()
            return FormalType(parameters=parameters, conclusion=conclusion)
        if header[index] not in "([{":
            index += 1
            continue
        close = {"(": ")", "{": "}", "[": "]"}[header[index]]
        content, next_index = _read_balanced(header, index, close)
        if content is None:
            break
        parameter = _parse_binder(content, _binder_name(header[index]))
        if parameter:
            parameters.append(parameter)
        index = next_index
    return FormalType(parameters=parameters, conclusion="")


def _parse_binder(content: str, binder: str) -> FormalParameter | None:
    content = content.strip()
    if not content:
        return None
    if ":" not in content:
        return FormalParameter(
            names=[],
            type=content,
            binder=binder,
            role="assumption" if binder == "instance" else "parameter",
        )
    names_text, type_text = content.split(":", 1)
    names = [
        name.strip()
        for name in names_text.split()
        if name.strip()
    ]
    type_text = type_text.strip()
    return FormalParameter(
        names=names,
        type=type_text,
        binder=binder,
        role=_classify_role(names, type_text, binder),
    )


def _classify_role(names: list[str], type_text: str, binder: str) -> str:
    if binder == "instance":
        return "assumption"
    if any(name.startswith("h") for name in names):
        return "assumption"
    if _looks_like_prop(type_text):
        return "assumption"
    return "parameter"


def _looks_like_prop(type_text: str) -> bool:
    normalized = _normalize(type_text)
    return (
        normalized == "Prop"
        or normalized.endswith(" Prop")
        or " = " in normalized
        or " ≠ " in normalized
        or " < " in normalized
        or " ≤ " in normalized
        or " >= " in normalized
        or " -> " in normalized
        or "↔" in normalized
        or "∧" in normalized
        or "∨" in normalized
    )


def _drop_decl_name(header: str) -> str:
    stripped = header.strip()
    if not stripped:
        return ""
    if stripped.startswith(":"):
        return stripped
    parts = stripped.split(None, 1)
    if len(parts) == 1:
        return ""
    return parts[1].strip()


def _starts_with_declaration_keyword(text: str) -> bool:
    return _declaration_prefix_end(text.strip()) is not None


def _drop_declaration_prefix(text: str) -> str:
    stripped = text.strip()
    prefix_end = _declaration_prefix_end(stripped)
    return stripped[prefix_end:].lstrip() if prefix_end is not None else stripped


def _declaration_prefix_end(text: str) -> int | None:
    index = 0
    while True:
        word, end = _read_word(text, index)
        if word not in DECLARATION_MODIFIERS:
            break
        index = _skip_spaces(text, end)
    word, end = _read_word(text, index)
    if word in DECLARATION_KEYWORDS:
        return end
    return None


def _read_word(text: str, start: int) -> tuple[str, int]:
    index = start
    while index < len(text) and (text[index].isalpha() or text[index] == "_"):
        index += 1
    return text[start:index], index


def _skip_spaces(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _read_balanced(text: str, start: int, close: str) -> tuple[str | None, int]:
    open_char = text[start]
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == open_char:
            depth += 1
        elif char == close:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return None, start + 1


def _binder_name(open_char: str) -> str:
    if open_char == "{":
        return "implicit"
    if open_char == "[":
        return "instance"
    return "explicit"


def _normalize(text: str) -> str:
    lines = [line.split("--", 1)[0] for line in text.strip().splitlines()]
    return " ".join(" ".join(lines).split())
