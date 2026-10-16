"""Tests for src/arms.py: system blocks, tools, executors and the ORACLE gold set.

Uses a small hand-made world in the SPEC section 1 shape, so no data files are needed.
"""

from __future__ import annotations

import pytest

from src import prompts
from src.arms import ARM_NAMES, build_arm, gold_memory_ids
from src.render import render_ctx, render_index, render_md, render_units
from src.tools import EMB_TOOLS, MD_TOOLS, EmbExecutor, MdExecutor


def make_world() -> dict:
    doctors = [
        {
            "doctor_id": "D01",
            "name": "Dr Anjali Mehta",
            "specialty": "Internal Medicine and Diabetology",
            "memories": [
                {
                    "memory_id": "D01-m01",
                    "date": "2025-04-01",
                    "type": "doctor_preference",
                    "author": "D01",
                    "text": "Prefers concise bullet-point summaries with dates.",
                }
            ],
        }
    ]
    patients = [
        {
            "patient_id": "P01",
            "name": "Sunita Rao",
            "age": 58,
            "sex": "F",
            "primary_doctor": "D01",
            "conditions": ["Type 2 diabetes", "Hypertension"],
            "memories": [
                {
                    "memory_id": "P01-m01",
                    "date": "2025-04-08",
                    "type": "visit_note",
                    "author": "D01",
                    "text": "S: k/c/o T2DM x 6 yrs, HTN. Metformin 500 mg BD, tolerating well.",
                },
                {
                    "memory_id": "P01-m02",
                    "date": "2026-04-14",
                    "type": "prescription",
                    "author": "D01",
                    "text": "Rx: Metformin stopped (GI upset). Start sitagliptin 50 mg OD.",
                },
            ],
        },
        {
            "patient_id": "P02",
            "name": "Ravi Menon",
            "age": 64,
            "sex": "M",
            "primary_doctor": "D01",
            "conditions": [],
            "memories": [
                {
                    "memory_id": "P02-m01",
                    "date": "2025-06-10",
                    "type": "allergy",
                    "author": "patient",
                    "text": "Penicillin: generalised rash (2019).",
                }
            ],
        },
    ]
    return {"patients": patients, "doctors": doctors, "level": "L1"}


WORLD_IDS = {"D01-m01", "P01-m01", "P01-m02", "P02-m01"}

QUESTION = {
    "qid": "q01",
    "asker_role": "doctor",
    "asker_id": "D01",
    "asker_name": "Dr Anjali Mehta",
    "question": "What's changed for Mrs Rao since her April visit?",
    "required_facts": [{"fact": "Metformin stopped on 2026-04-14", "memory_ids": ["P01-m02"]}],
    "forbidden_facts": ["States she is still on metformin"],
}


class StubStore:
    """Stands in for EmbeddingStore: records search calls and returns one fixed hit."""

    def __init__(self):
        self.calls: list[dict] = []

    def search(self, query, k=8, patient_id=None, allowed_ids=None):
        self.calls.append({"query": query, "k": k, "patient_id": patient_id, "allowed_ids": allowed_ids})
        return [{"memory_id": "P01-m02", "score": 0.5, "text": "Rx: Metformin stopped."}]


@pytest.fixture
def world():
    return make_world()


# ---------------------------------------------------------------- shared shape


@pytest.mark.parametrize("name", ARM_NAMES)
def test_system_has_base_block_then_arm_block_with_cache_control(world, name):
    arm = build_arm(name, world, QUESTION, emb_store=StubStore())
    assert arm.name == name
    assert len(arm.system) == 2
    assert arm.system[0] == {"type": "text", "text": prompts.BASE}
    assert "cache_control" not in arm.system[0]
    assert arm.system[1]["type"] == "text"
    assert arm.system[1]["cache_control"] == {"type": "ephemeral"}
    assert arm.system[1]["text"].startswith(prompts.ARM_TEXT[name])


def test_unknown_arm_name_raises(world):
    with pytest.raises(ValueError, match="unknown arm"):
        build_arm("RAG", world, QUESTION)


# ---------------------------------------------------------------- CTX / FLOOR


def test_ctx_puts_the_whole_store_in_the_system_prompt(world):
    arm = build_arm("CTX", world, QUESTION)
    assert arm.tools is None
    assert arm.execute is None
    assert arm.system[1]["text"] == prompts.ARM_CTX + render_ctx(world)
    for memory_text in ("Prefers concise", "Penicillin: generalised rash", "Rx: Metformin stopped"):
        assert memory_text in arm.system[1]["text"]


def test_floor_has_no_memory_and_no_tools(world):
    arm = build_arm("FLOOR", world, QUESTION)
    assert arm.tools is None
    assert arm.execute is None
    assert arm.system[1]["text"] == prompts.ARM_FLOOR


# ---------------------------------------------------------------- EMB


def test_emb_uses_search_tool_with_no_records_up_front(world):
    arm = build_arm("EMB", world, QUESTION, emb_store=StubStore())
    assert arm.tools == EMB_TOOLS
    assert isinstance(arm.execute, EmbExecutor)
    assert arm.system[1]["text"] == prompts.ARM_EMB
    assert arm.execute.allowed_ids == WORLD_IDS  # doctor units included


def test_emb_executor_searches_only_the_ids_in_this_world(world):
    store = StubStore()
    arm = build_arm("EMB", world, QUESTION, emb_store=store)
    text, accessed = arm.execute("search_memory", {"query": "sugar levels"})
    assert store.calls[-1]["allowed_ids"] == WORLD_IDS
    assert accessed == ["P01-m02"]
    assert text.startswith("Rx: Metformin stopped.")


def test_emb_arm_needs_a_store(world):
    with pytest.raises(ValueError, match="EmbeddingStore"):
        build_arm("EMB", world, QUESTION)


# ---------------------------------------------------------------- MD


def test_md_puts_the_index_up_front_and_serves_the_files(world):
    arm = build_arm("MD", world, QUESTION)
    assert arm.tools == MD_TOOLS
    assert isinstance(arm.execute, MdExecutor)
    assert arm.system[1]["text"] == prompts.ARM_MD + render_index(world)

    files = render_md(world)
    text, accessed = arm.execute("read_file", {"path": "patients/P01-sunita-rao.md"})
    assert text == files["patients/P01-sunita-rao.md"]
    assert accessed == ["P01-m01", "P01-m02"]

    text, accessed = arm.execute("grep", {"pattern": "Metformin"})
    assert accessed == ["P01-m01", "P01-m02"]


def test_md_index_is_the_full_index_file(world):
    arm = build_arm("MD", world, QUESTION)
    text, accessed = arm.execute("read_file", {"path": "INDEX.md"})
    assert text == render_md(world)["INDEX.md"]
    assert accessed == []


# ---------------------------------------------------------------- ORACLE


def test_gold_memory_ids_is_the_union_of_required_facts():
    question = {
        "required_facts": [
            {"fact": "a", "memory_ids": ["P01-m02"]},
            {"fact": "b", "memory_ids": ["P01-m01", "P02-m01"]},
            {"fact": "c", "memory_ids": ["P01-m02"]},
        ]
    }
    assert gold_memory_ids(question) == {"P01-m01", "P01-m02", "P02-m01"}
    assert gold_memory_ids({"required_facts": []}) == set()


def test_oracle_shows_only_the_gold_memories(world):
    arm = build_arm("ORACLE", world, QUESTION)
    assert arm.tools is None
    assert arm.execute is None
    text = arm.system[1]["text"]
    assert text.startswith(prompts.ARM_ORACLE)
    assert "Rx: Metformin stopped (GI upset)." in text
    assert "S: k/c/o T2DM x 6 yrs" not in text  # P01-m01 is not gold
    assert "Prefers concise" not in text  # D01-m01 is not gold
    assert "Penicillin" not in text  # P02-m01 is not gold


def test_oracle_with_no_gold_memories_has_no_records(world):
    arm = build_arm("ORACLE", world, {"required_facts": []})
    assert "<file path=" not in arm.system[1]["text"]


def test_oracle_rejects_gold_ids_missing_from_the_world(world):
    question = {"required_facts": [{"fact": "x", "memory_ids": ["P09-m01"]}]}
    with pytest.raises(ValueError, match="P09-m01"):
        build_arm("ORACLE", world, question)


# ---------------------------------------------------------------- units


def test_emb_allowed_ids_match_render_units(world):
    assert {unit["memory_id"] for unit in render_units(world)} == WORLD_IDS
