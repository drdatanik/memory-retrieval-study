"""Choose the nested distractor sets for levels L2 to L5, sized by the CTX prompt.

    python -m src.size_levels [--dry-run]

For each target in config.LEVEL_TARGETS, binary-searches the largest n whose CTX system prompt
(core world plus the first n distractors, sorted by ID) is at most the target in tokens. Tokens are
counted locally with the subject model's tokenizer (config.SUBJECT_TOKENIZER_REPO, read from the
local Hugging Face cache), so no API is called. Writes world/levels.json. The sets are nested by
construction (each level is a prefix of the sorted ID list). A dry run estimates tokens as
len(text)/3.24, prints the table only and does not write levels.json, because its numbers are
estimates. With no distractors yet, a dry run sizes L1 (core only) and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from src import config, llm
from src import world as world_mod

ROOT = Path(__file__).resolve().parent.parent
DISTRACTOR_DIR = ROOT / "world" / "distractors"
LEVELS_PATH = ROOT / "world" / "levels.json"
TOKENIZER_CACHE = ROOT / "runs" / "hf_cache"
DRY_RUN_CHARS_PER_TOKEN = 3.24


class Sizer:
    """Memoised token count as a function of n. Each n is counted at most once."""

    def __init__(self, count_fn):
        self._count_fn = count_fn
        self.cache: dict[int, int] = {}

    def __call__(self, n: int) -> int:
        if n not in self.cache:
            self.cache[n] = self._count_fn(n)
        return self.cache[n]


def largest_n_within(sizer: Sizer, target: int, n_max: int) -> int:
    """Largest n in [0, n_max] with sizer(n) <= target, by binary search.

    Assumes sizer is non-decreasing in n. If even the full n_max fits, returns n_max.
    """
    if sizer(0) > target:
        raise ValueError(f"core-only prompt is {sizer(0)} tokens, already above the target {target}")
    lo, hi = 0, n_max
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if sizer(mid) <= target:
            lo = mid
        else:
            hi = mid - 1
    return lo


def choose_levels(sizer: Sizer, n_max: int, targets: dict) -> dict[str, int]:
    """Map each level to its number of distractors. A None target (L1) means no distractors."""
    return {
        level: 0 if target is None else largest_n_within(sizer, target, n_max)
        for level, target in targets.items()
    }


def levels_payload(ids: list[str], chosen: dict[str, int], sizer: Sizer) -> dict:
    """The world/levels.json structure. Each level's distractors are a prefix of ids."""
    return {
        level: {"distractors": ids[:n], "ctx_tokens": sizer(n)}
        for level, n in chosen.items()
    }


# --- Real world and system prompt -----------------------------------------------------------------
def distractor_ids() -> list[str]:
    return sorted(p.stem for p in DISTRACTOR_DIR.glob("X*.json"))


class WorldLoader:
    """Loads the core world and every distractor once, then builds the world for any prefix."""

    def __init__(self, ids: list[str] | None = None):
        self.core_patients, self.core_doctors = world_mod.load_core()
        ids = distractor_ids() if ids is None else ids
        loaded = world_mod.load_distractors(ids)
        self.distractors = sorted(loaded, key=lambda p: p["patient_id"])
        self.ids = [p["patient_id"] for p in self.distractors]
        self._core_memories = _memory_count(self.core_patients) + _memory_count(self.core_doctors)
        self._prefix_memories = [0]
        for p in self.distractors:
            self._prefix_memories.append(self._prefix_memories[-1] + len(p["memories"]))

    def world(self, n: int) -> dict:
        return {
            "patients": self.core_patients + self.distractors[:n],
            "doctors": self.core_doctors,
            "level": f"N{n}",
        }

    def memories(self, n: int) -> int:
        return self._core_memories + self._prefix_memories[n]


def _memory_count(records: list[dict]) -> int:
    return sum(len(r["memories"]) for r in records)


def ctx_system(world: dict) -> list[dict]:
    """System blocks exactly as the CTX arm builds them (src.arms.build_arm; the question is unused for CTX)."""
    from src.arms import build_arm  # imported here: arms pulls in the embedding and tool modules

    return build_arm("CTX", world, {}).system


def load_tokenizer():
    """The subject model's tokenizer. Read from the local cache only, so nothing is downloaded and the
    tokenizer cannot silently change to a newer revision of the repo between runs."""
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    path = hf_hub_download(config.SUBJECT_TOKENIZER_REPO, "tokenizer.json",
                           cache_dir=str(TOKENIZER_CACHE), local_files_only=True)
    return Tokenizer.from_file(path)


def count_tokens(tokenizer, text: str) -> int:
    """Token count of text under a tokenizers.Tokenizer (or any object with .encode(text).ids)."""
    return len(tokenizer.encode(text).ids)


def make_counter(loader: WorldLoader, dry_run: bool, tokenizer=None):
    """n -> token count of the CTX system prompt for the core world plus the first n distractors.

    The text is llm.system_text of the CTX blocks, which is the one system string the subject model is sent.
    A dry run uses len(text)/3.24 and loads no tokenizer; a live count uses `tokenizer` if given, else
    the cached subject tokenizer.
    """
    if dry_run:
        def count(n: int) -> int:
            return int(len(llm.system_text(ctx_system(loader.world(n)))) / DRY_RUN_CHARS_PER_TOKEN)

        return count

    tok = tokenizer if tokenizer is not None else load_tokenizer()

    def count(n: int) -> int:
        return count_tokens(tok, llm.system_text(ctx_system(loader.world(n))))

    return count


# --- Output ---------------------------------------------------------------------------------------
def format_table(rows: list[tuple]) -> str:
    header = ("level", "target", "distractors", "memories", "ctx_tokens")
    cells = [header] + [tuple(str(x) for x in row) for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(header))]
    return "\n".join("  ".join(val.ljust(widths[i]) for i, val in enumerate(row)).rstrip() for row in cells)


def write_json_atomic(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Size the distractor levels by CTX prompt tokens.")
    parser.add_argument("--dry-run", action="store_true",
                        help="estimate tokens as len(text)/3.24 instead of the tokenizer; does not write levels.json")
    args = parser.parse_args(argv)

    ids = distractor_ids()
    if not ids and not args.dry_run:
        print("no distractor files in world/distractors; run python -m src.gen_distractors first", file=sys.stderr)
        return 1
    loader = WorldLoader(ids)
    n_max = len(loader.ids)
    sizer = Sizer(make_counter(loader, dry_run=args.dry_run))

    if n_max:
        targets = config.LEVEL_TARGETS
    else:  # dry run before any generation: only the core-only level can be sized
        targets = {level: t for level, t in config.LEVEL_TARGETS.items() if t is None}
        print("no distractor files yet, so only L1 (core only) is sized; L2 to L5 need generated distractors",
              file=sys.stderr)

    chosen = choose_levels(sizer, n_max, targets)
    payload = levels_payload(loader.ids, chosen, sizer)

    rows = []
    for level, target in targets.items():
        n = chosen[level]
        rows.append((level, "-" if target is None else target, n, loader.memories(n), sizer(n)))
    print(format_table(rows))
    capped = [level for level, target in targets.items()
              if target is not None and chosen[level] == n_max and sizer(n_max) <= target]
    if capped:
        print(f"warning: {', '.join(capped)} target not reached even with all {n_max} distractors; "
              f"generate more to reach it", file=sys.stderr)
    counting = "dry run, len/3.24" if args.dry_run else f"{config.SUBJECT_TOKENIZER_REPO} tokenizer"
    print(f"counted {len(sizer.cache)} distinct prompt sizes ({counting})")

    if args.dry_run:
        print("dry run: world/levels.json not written")
    else:
        write_json_atomic(LEVELS_PATH, payload)
        print(f"wrote {LEVELS_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
