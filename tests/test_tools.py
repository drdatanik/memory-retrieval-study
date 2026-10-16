"""Tests for src/tools.py: tool schemas, the MD executor (read_file, grep) and the EMB executor.

Uses a hand-made `files` dict and a fake store, so no model is needed.
"""

from __future__ import annotations

import pytest

from src import config
from src.tools import EMB_TOOLS, MD_TOOLS, EmbExecutor, MdExecutor

FILES = {
    "INDEX.md": (
        "# Record index: Sunrise Family Clinic\n"
        "- doctors/D01-anjali-mehta.md: Dr Anjali Mehta (D01), Internal Medicine and Diabetology\n"
        "- patients/P01-sunita-rao.md: Sunita Rao (P01), 58 F, primary D01\n"
        "- patients/P02-ravi-menon.md: Ravi Menon (P02), 64 M, primary D01\n"
    ),
    "doctors/D01-anjali-mehta.md": (
        "# Dr Anjali Mehta (D01): Internal Medicine and Diabetology\n"
        "\n"
        "## 2025-04-01 · Doctor preference · Dr Anjali Mehta (D01) <!-- m:D01-m01 -->\n"
        "Prefers concise bullet-point summaries with dates.\n"
    ),
    "patients/P01-sunita-rao.md": (
        "# Sunita Rao (P01)\n"  # 1
        "58 F · Primary doctor: Dr Anjali Mehta (D01) · Conditions: Type 2 diabetes, Hypertension\n"  # 2
        "\n"  # 3
        "## 2025-04-08 · Visit note · Dr Anjali Mehta (D01) <!-- m:P01-m01 -->\n"  # 4
        "S: k/c/o T2DM x 6 yrs, HTN. Metformin 500 mg BD, tolerating well.\n"  # 5
        "\n"  # 6
        "## 2026-04-14 · Prescription · Dr Anjali Mehta (D01) <!-- m:P01-m02 -->\n"  # 7
        "Rx: Metformin stopped (GI upset). Start sitagliptin 50 mg OD.\n"  # 8
    ),
    "patients/P02-ravi-menon.md": (
        "# Ravi Menon (P02)\n"
        "\n"
        "## 2025-06-10 · Allergy · Dr Anjali Mehta (D01) <!-- m:P02-m01 -->\n"
        "Penicillin: generalised rash (2019).\n"
    ),
}


def _big_file() -> str:
    """One patient file with two memories of 60 matching lines each."""
    lines = ["# Big patient (P03)", "", "## 2025-05-01 · Visit note · Dr Anjali Mehta (D01) <!-- m:P03-m01 -->"]
    lines += [f"hit alpha {i:02d}" for i in range(60)]
    lines += ["", "## 2025-06-01 · Visit note · Dr Anjali Mehta (D01) <!-- m:P03-m02 -->"]
    lines += [f"hit beta {i:02d}" for i in range(60)]
    return "\n".join(lines) + "\n"


BIG_FILES = {"patients/P03-big.md": _big_file()}


@pytest.fixture
def md():
    return MdExecutor(FILES)


class FakeStore:
    """Stands in for EmbeddingStore: records each search call and returns canned hits."""

    def __init__(self, hits=None, error=None):
        self.hits = hits if hits is not None else []
        self.error = error
        self.calls: list[dict] = []

    def search(self, query, k=8, patient_id=None, allowed_ids=None):
        self.calls.append({"query": query, "k": k, "patient_id": patient_id, "allowed_ids": allowed_ids})
        if self.error is not None:
            raise self.error
        return list(self.hits)


HITS = [
    {
        "memory_id": "P01-m01",
        "score": 0.87312,
        "text": "[P01-m01] 2026-06-02 · Lab result · Patient: Sunita Rao (P01, 58 F) · Author: Lab\nHbA1c 8.1 %",
    },
    {
        "memory_id": "P01-m02",
        "score": 0.5,
        "text": "[P01-m02] 2026-04-14 · Prescription · Patient: Sunita Rao (P01, 58 F)\nRx: Metformin stopped.",
    },
]
ALLOWED = {"P01-m01", "P01-m02", "P02-m01", "D01-m01"}


# ---------------------------------------------------------------- schemas


def test_tool_names():
    assert [t["name"] for t in EMB_TOOLS] == ["search_memory"]
    assert [t["name"] for t in MD_TOOLS] == ["read_file", "grep"]


@pytest.mark.parametrize("tool", EMB_TOOLS + MD_TOOLS, ids=lambda t: t["name"])
def test_schema_is_closed_and_required_fields_exist(tool):
    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) <= set(schema["properties"])


def test_required_fields():
    assert EMB_TOOLS[0]["input_schema"]["required"] == ["query"]
    assert MD_TOOLS[0]["input_schema"]["required"] == ["path"]
    assert MD_TOOLS[1]["input_schema"]["required"] == ["pattern"]


def test_optional_fields_are_declared():
    search_props = EMB_TOOLS[0]["input_schema"]["properties"]
    assert search_props["k"]["type"] == "integer"
    assert search_props["patient_id"]["type"] == "string"
    grep_props = MD_TOOLS[1]["input_schema"]["properties"]
    assert grep_props["path"]["type"] == "string"


@pytest.mark.parametrize(
    "tool, low, high",
    [(EMB_TOOLS[0], 60, 90), (MD_TOOLS[0], 30, 45), (MD_TOOLS[1], 30, 45)],
    ids=["search_memory", "read_file", "grep"],
)
def test_description_word_counts(tool, low, high):
    words = len(tool["description"].split())
    assert low <= words <= high, f"{tool['name']} description has {words} words"


# ---------------------------------------------------------------- read_file


def test_read_file_hit_returns_content_and_accessed_ids(md):
    text, accessed = md("read_file", {"path": "patients/P01-sunita-rao.md"})
    assert text == FILES["patients/P01-sunita-rao.md"]
    assert accessed == ["P01-m01", "P01-m02"]


@pytest.mark.parametrize("path", ["./patients/P01-sunita-rao.md", "/patients/P01-sunita-rao.md"])
def test_read_file_strips_leading_dot_slash_and_slash(md, path):
    text, accessed = md("read_file", {"path": path})
    assert text == FILES["patients/P01-sunita-rao.md"]
    assert accessed == ["P01-m01", "P01-m02"]


def test_read_file_doctor_file_accessed_ids(md):
    _, accessed = md("read_file", {"path": "doctors/D01-anjali-mehta.md"})
    assert accessed == ["D01-m01"]


def test_read_file_index_has_no_accessed_ids(md):
    text, accessed = md("read_file", {"path": "INDEX.md"})
    assert text == FILES["INDEX.md"]
    assert accessed == []


def test_read_file_miss_returns_error_and_no_ids(md):
    text, accessed = md("read_file", {"path": "patients/P99-nobody.md"})
    assert text == "Error: no such file: patients/P99-nobody.md. Check INDEX.md."
    assert accessed == []


# ---------------------------------------------------------------- grep


def test_grep_is_case_insensitive_and_maps_lines_to_memories(md):
    text, accessed = md("grep", {"pattern": "METFORMIN"})
    assert text == (
        "patients/P01-sunita-rao.md:5: S: k/c/o T2DM x 6 yrs, HTN. Metformin 500 mg BD, tolerating well.\n"
        "patients/P01-sunita-rao.md:8: Rx: Metformin stopped (GI upset). Start sitagliptin 50 mg OD."
    )
    assert accessed == ["P01-m01", "P01-m02"]


def test_grep_output_lines_have_path_lineno_prefix(md):
    text, _ = md("grep", {"pattern": "Prefers|Penicillin"})
    lines = text.split("\n")
    assert lines[0] == "doctors/D01-anjali-mehta.md:4: Prefers concise bullet-point summaries with dates."
    assert lines[1] == "patients/P02-ravi-menon.md:4: Penicillin: generalised rash (2019)."


def test_grep_invalid_regex_falls_back_to_literal_search(md):
    text, accessed = md("grep", {"pattern": "(GI upset"})
    assert text == "patients/P01-sunita-rao.md:8: Rx: Metformin stopped (GI upset). Start sitagliptin 50 mg OD."
    assert accessed == ["P01-m02"]


def test_grep_path_filter_restricts_to_folder_prefix(md):
    text, accessed = md("grep", {"pattern": "Prefers", "path": "patients/"})
    assert text == "No matches."
    assert accessed == []

    text, accessed = md("grep", {"pattern": "Prefers", "path": "doctors/"})
    assert text == "doctors/D01-anjali-mehta.md:4: Prefers concise bullet-point summaries with dates."
    assert accessed == ["D01-m01"]


def test_grep_path_filter_restricts_to_one_file(md):
    text, _ = md("grep", {"pattern": "Penicillin", "path": "patients/P01-sunita-rao.md"})
    assert text == "No matches."

    text, accessed = md("grep", {"pattern": "metformin", "path": "./patients/P01-sunita-rao.md"})
    assert text.count("\n") == 1
    assert accessed == ["P01-m01", "P01-m02"]


def test_grep_line_before_any_marker_maps_to_no_memory(md):
    # The title line of P01 and the INDEX line both match, but neither sits under a record marker.
    text, accessed = md("grep", {"pattern": "Sunita Rao"})
    assert "patients/P01-sunita-rao.md:1: # Sunita Rao (P01)" in text
    assert accessed == []


def test_grep_no_matches(md):
    assert md("grep", {"pattern": "zzz-not-present"}) == ("No matches.", [])


def test_grep_caps_output_at_50_lines_and_counts_the_rest():
    md = MdExecutor(BIG_FILES)
    text, accessed = md("grep", {"pattern": "hit"})
    lines = text.split("\n")
    assert len(lines) == 51
    assert lines[-1] == "... 70 more matches not shown"
    assert lines[0] == "patients/P03-big.md:4: hit alpha 00"
    # Only the lines the model sees count as accessed: the first 50 are all under P03-m01.
    assert accessed == ["P03-m01"]


def test_grep_accessed_ids_follow_the_nearest_marker_above():
    md = MdExecutor(BIG_FILES)
    text, accessed = md("grep", {"pattern": "beta 0"})  # 10 matches, under the cap
    assert len(text.split("\n")) == 10
    assert "more matches not shown" not in text
    assert accessed == ["P03-m02"]

    text, accessed = md("grep", {"pattern": "beta"})  # 60 matches, so 50 shown
    assert text.split("\n")[-1] == "... 10 more matches not shown"
    assert accessed == ["P03-m02"]


def test_grep_scoped_to_one_memory_marks_only_that_memory():
    md = MdExecutor(BIG_FILES)
    text, accessed = md("grep", {"pattern": "beta 05"})
    assert text == "patients/P03-big.md:71: hit beta 05"
    assert accessed == ["P03-m02"]


# ---------------------------------------------------------------- MD errors


def test_unknown_md_tool_returns_error(md):
    assert md("delete_file", {"path": "INDEX.md"}) == ("Error: unknown tool: delete_file", [])


@pytest.mark.parametrize(
    "name, tool_input",
    [
        ("read_file", None),
        ("read_file", {}),
        ("read_file", {"path": ""}),
        ("read_file", {"path": 7}),
        ("grep", None),
        ("grep", "pattern"),
        ("grep", {}),
        ("grep", {"pattern": ""}),
        ("grep", {"pattern": 5}),
        ("grep", {"pattern": "x", "path": 7}),
        (None, None),
    ],
)
def test_md_executor_returns_error_string_for_bad_input(name, tool_input):
    text, accessed = MdExecutor(FILES)(name, tool_input)
    assert text.startswith("Error:")
    assert accessed == []


# ---------------------------------------------------------------- EMB


def test_search_formats_hits_with_relevance_and_reports_ids():
    store = FakeStore(hits=HITS)
    text, accessed = EmbExecutor(store, ALLOWED)("search_memory", {"query": "sugar levels"})
    expected = (
        f"{HITS[0]['text']}\n(relevance 0.873)"
        "\n---\n"
        f"{HITS[1]['text']}\n(relevance 0.500)"
    )
    assert text == expected
    assert accessed == ["P01-m01", "P01-m02"]


def test_search_passes_allowed_ids_and_default_k():
    store = FakeStore(hits=HITS)
    EmbExecutor(store, ALLOWED)("search_memory", {"query": "sugar levels"})
    call = store.calls[-1]
    assert call["query"] == "sugar levels"
    assert call["k"] == config.SEARCH_K
    assert call["patient_id"] is None
    assert call["allowed_ids"] == ALLOWED


def test_search_no_hits():
    assert EmbExecutor(FakeStore(hits=[]), ALLOWED)("search_memory", {"query": "x"}) == (
        "No matching records.",
        [],
    )


@pytest.mark.parametrize(
    "k_in, k_sent",
    [(0, 1), (-3, 1), (1, 1), (5, 5), (20, 20), (50, 20), ("7", 7), (8.0, 8)],
)
def test_k_is_clamped_to_1_to_20(k_in, k_sent):
    store = FakeStore(hits=HITS)
    EmbExecutor(store, ALLOWED)("search_memory", {"query": "x", "k": k_in})
    assert store.calls[-1]["k"] == k_sent


def test_patient_id_is_passed_through():
    store = FakeStore(hits=HITS)
    EmbExecutor(store, ALLOWED)("search_memory", {"query": "x", "patient_id": "P02"})
    assert store.calls[-1]["patient_id"] == "P02"


def test_empty_patient_id_means_no_filter():
    store = FakeStore(hits=HITS)
    EmbExecutor(store, ALLOWED)("search_memory", {"query": "x", "patient_id": ""})
    assert store.calls[-1]["patient_id"] is None


@pytest.mark.parametrize(
    "tool_input",
    [
        None,
        "sugar levels",
        {},
        {"query": ""},
        {"query": "   "},
        {"query": 3},
        {"query": "x", "k": "many"},
        {"query": "x", "k": True},
        {"query": "x", "k": 2.5},
        {"query": "x", "patient_id": 5},
    ],
)
def test_bad_search_input_returns_error_and_never_calls_store(tool_input):
    store = FakeStore(hits=HITS)
    text, accessed = EmbExecutor(store, ALLOWED)("search_memory", tool_input)
    assert text.startswith("Error:")
    assert accessed == []
    assert store.calls == []


def test_unknown_emb_tool_returns_error():
    store = FakeStore(hits=HITS)
    assert EmbExecutor(store, ALLOWED)("get_record", {"query": "x"}) == ("Error: unknown tool: get_record", [])
    assert store.calls == []


def test_store_failure_is_reported_not_raised():
    store = FakeStore(error=RuntimeError("boom"))
    text, accessed = EmbExecutor(store, ALLOWED)("search_memory", {"query": "x"})
    assert text == "Error: RuntimeError: boom"
    assert accessed == []
