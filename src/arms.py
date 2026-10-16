"""Arm construction: system blocks, tools and executor for each study arm (SPEC section 3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src import prompts
from src.embed_index import EmbeddingStore
from src.render import render_ctx, render_index, render_md, render_memories_md, render_units
from src.tools import EMB_TOOLS, MD_TOOLS, EmbExecutor, MdExecutor
from src.world import all_memories

ARM_NAMES = ("CTX", "EMB", "MD", "ORACLE", "FLOOR")


@dataclass
class Arm:
    name: str  # CTX | EMB | MD | ORACLE | FLOOR
    system: list[dict]  # Anthropic system text blocks; the last block carries cache_control
    tools: list[dict] | None
    execute: Callable[[str, dict], tuple[str, list[str]]] | None


def gold_memory_ids(question: dict) -> set[str]:
    """The union of required_facts[*].memory_ids (SPEC section 2)."""
    return {
        memory_id
        for fact in question.get("required_facts", [])
        for memory_id in fact.get("memory_ids", [])
    }


def _system(arm_name: str, payload: str) -> list[dict]:
    return [
        {"type": "text", "text": prompts.BASE},
        {
            "type": "text",
            "text": prompts.ARM_TEXT[arm_name] + payload,
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _oracle_memories(world: dict, gold_ids: set[str]) -> list[dict]:
    memories = [m for m in all_memories(world) if m["memory_id"] in gold_ids]
    missing = gold_ids - {m["memory_id"] for m in memories}
    if missing:
        raise ValueError(f"gold memory IDs not in this world: {sorted(missing)}")
    return memories


def build_arm(
    name: str,
    world: dict,
    question: dict,
    emb_store: EmbeddingStore | None = None,
) -> Arm:
    """Build one arm for one question against one world.

    EMB needs `emb_store` to have been built already (`emb_store.build(render_units(world))`).
    This function does not build it.
    """
    if name not in ARM_NAMES:
        raise ValueError(f"unknown arm: {name!r}")

    if name == "CTX":
        return Arm("CTX", _system("CTX", render_ctx(world)), None, None)

    if name == "EMB":
        if emb_store is None:
            raise ValueError("the EMB arm needs an EmbeddingStore")
        allowed_ids = {unit["memory_id"] for unit in render_units(world)}
        return Arm("EMB", _system("EMB", ""), EMB_TOOLS, EmbExecutor(emb_store, allowed_ids))

    if name == "MD":
        return Arm("MD", _system("MD", render_index(world)), MD_TOOLS, MdExecutor(render_md(world)))

    if name == "ORACLE":
        memories = _oracle_memories(world, gold_memory_ids(question))
        return Arm("ORACLE", _system("ORACLE", render_memories_md(memories, world)), None, None)

    return Arm("FLOOR", _system("FLOOR", ""), None, None)
