"""Tool definitions and executors for the EMB and MD arms.

An executor is called as `executor(name, tool_input)` and returns `(result_text, accessed_memory_ids)`.
Executors never raise. An unknown tool or bad input returns "Error: ..." with no accessed IDs.
"""

from __future__ import annotations

import re
from typing import Any

from src import config
from src.embed_index import EmbeddingStore

SEARCH_K_MIN = 1
SEARCH_K_MAX = 20
GREP_LINE_CAP = 50
NO_RECORDS = "No matching records."
NO_MATCHES = "No matches."
RESULT_SEPARATOR = "\n---\n"

SEARCH_MEMORY = "search_memory"
READ_FILE = "read_file"
GREP = "grep"

# <!-- m:P01-m01 --> heading marker that ends each record in a Markdown file.
_MARKER = re.compile(r"<!--\s*m:([^\s>]+)\s*-->")


class _BadInput(ValueError):
    """Raised inside an executor for input the model should correct; becomes an Error string."""


# ---------------------------------------------------------------- definitions

EMB_TOOLS: list[dict] = [
    {
        "name": SEARCH_MEMORY,
        "description": (
            "Searches every record in the clinic and returns the closest matches in meaning, best first. "
            "Each result gives the record's header (date, type, patient, author and record ID) and its full "
            "text, followed by a relevance score. The query does not need the record's exact wording. "
            "Set patient_id to limit results to one patient's records; doctor records "
            "are then left out. Ranking reflects similarity only, so a result is not guaranteed to answer the "
            "question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for, as a short phrase or description.",
                },
                "k": {
                    "type": "integer",
                    "description": (
                        f"How many results to return, from {SEARCH_K_MIN} to {SEARCH_K_MAX}. "
                        f"Defaults to {config.SEARCH_K}."
                    ),
                },
                "patient_id": {
                    "type": "string",
                    "description": "Optional patient ID, such as P01. Limits results to that patient's records.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]

MD_TOOLS: list[dict] = [
    {
        "name": READ_FILE,
        "description": (
            "Opens one record file by its exact path and returns the whole file. Use a path listed in "
            "INDEX.md. Each record in the file is headed with its date, type, author and record ID. "
            "A path that matches no file returns an error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Exact file path from INDEX.md, such as patients/P01-sunita-rao.md.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": GREP,
        "description": (
            "Searches record files for a case-insensitive regular expression. If the pattern is not valid "
            "regex, it is matched as plain text. Returns up to 50 matching lines with file and line number. "
            "Set path to search one file or a folder, such as patients/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression, or plain text if it is not a valid regular expression.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional file path, or folder prefix such as patients/.",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
]


# ---------------------------------------------------------------- helpers


def _unique(items) -> list[str]:
    return list(dict.fromkeys(items))


def _parse_k(value: Any) -> int:
    if value is None:
        return config.SEARCH_K
    if isinstance(value, bool):
        raise _BadInput("k must be an integer.")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise _BadInput("k must be an integer.")
    return min(max(value, SEARCH_K_MIN), SEARCH_K_MAX)


def _require_str(tool_input: Any, key: str, required: bool) -> str | None:
    if not isinstance(tool_input, dict):
        raise _BadInput("tool input must be a JSON object.")
    value = tool_input.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise _BadInput(f"{key} must be a string.")
    return value


def _clean_path(path: str) -> str:
    """Strip leading './' and '/' so 'patients/x.md', './patients/x.md' and '/patients/x.md' match."""
    p = path.strip()
    while True:
        if p.startswith("./"):
            p = p[2:]
        elif p.startswith("/"):
            p = p[1:]
        else:
            return p


# ---------------------------------------------------------------- EMB


class EmbExecutor:
    """Runs `search_memory` against an EmbeddingStore, restricted to `allowed_ids`."""

    def __init__(self, store: EmbeddingStore, allowed_ids: set[str]):
        self.store = store
        self.allowed_ids = set(allowed_ids)

    def __call__(self, name: str, tool_input: Any) -> tuple[str, list[str]]:
        try:
            if name != SEARCH_MEMORY:
                return f"Error: unknown tool: {name}", []
            return self._search_memory(tool_input)
        except _BadInput as exc:
            return f"Error: {exc}", []
        except Exception as exc:  # the loop must always get a result string
            return f"Error: {type(exc).__name__}: {exc}", []

    def _search_memory(self, tool_input: Any) -> tuple[str, list[str]]:
        query = _require_str(tool_input, "query", required=True)
        if not query.strip():
            raise _BadInput("query must be a non-empty string.")
        k = _parse_k(tool_input.get("k"))
        patient_id = _require_str(tool_input, "patient_id", required=False) or None

        hits = self.store.search(query, k=k, patient_id=patient_id, allowed_ids=self.allowed_ids)
        if not hits:
            return NO_RECORDS, []
        text = RESULT_SEPARATOR.join(f"{hit['text']}\n(relevance {hit['score']:.3f})" for hit in hits)
        return text, _unique(hit["memory_id"] for hit in hits)


# ---------------------------------------------------------------- MD


class MdExecutor:
    """Runs `read_file` and `grep` over an in-memory {relative_path: content} store."""

    def __init__(self, files: dict[str, str]):
        self.files = dict(files)

    def __call__(self, name: str, tool_input: Any) -> tuple[str, list[str]]:
        try:
            if name == READ_FILE:
                return self._read_file(tool_input)
            if name == GREP:
                return self._grep(tool_input)
            return f"Error: unknown tool: {name}", []
        except _BadInput as exc:
            return f"Error: {exc}", []
        except Exception as exc:  # the loop must always get a result string
            return f"Error: {type(exc).__name__}: {exc}", []

    def _read_file(self, tool_input: Any) -> tuple[str, list[str]]:
        path = _require_str(tool_input, "path", required=True)
        if not path.strip():
            raise _BadInput("path must be a non-empty string.")
        key = _clean_path(path)
        if key not in self.files:
            return f"Error: no such file: {path}. Check INDEX.md.", []
        content = self.files[key]
        return content, _unique(_MARKER.findall(content))

    def _grep(self, tool_input: Any) -> tuple[str, list[str]]:
        pattern = _require_str(tool_input, "pattern", required=True)
        if not pattern:
            raise _BadInput("pattern must be a non-empty string.")
        scope = _require_str(tool_input, "path", required=False) or ""
        regex = _compile(pattern)
        prefix = _clean_path(scope)

        # A file matches if its path equals `prefix` or starts with it, so the prefix can be a
        # whole file or a folder such as 'patients/'. Files are searched in the order given.
        keys = [key for key in self.files if key.startswith(prefix)]

        shown: list[str] = []
        accessed: list[str] = []
        total = 0
        for key in keys:
            current: str | None = None  # memory whose marker is nearest above the current line
            for lineno, line in enumerate(self.files[key].splitlines(), start=1):
                markers = _MARKER.findall(line)
                if markers:
                    current = markers[-1]
                if not regex.search(line):
                    continue
                total += 1
                if len(shown) < GREP_LINE_CAP:
                    shown.append(f"{key}:{lineno}: {line}")
                    if current is not None:
                        accessed.append(current)

        if total == 0:
            return NO_MATCHES, []
        text = "\n".join(shown)
        if total > len(shown):
            text += f"\n... {total - len(shown)} more matches not shown"
        return text, _unique(accessed)


def _compile(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)
