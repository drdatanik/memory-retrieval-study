"""Loaders for the synthetic clinic world: core patients and doctors, distractor patients, nested levels.

Every loader takes world_dir (default ROOT/world) so tests can point at a fixture.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORLD_DIR = ROOT / "world"


def _read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_core(world_dir: Path = WORLD_DIR) -> tuple[list[dict], list[dict]]:
    """Return (patient files P*.json, doctor files D*.json) from world_dir/core, each sorted by ID."""
    core = Path(world_dir) / "core"
    patients = [_read_json(p) for p in sorted(core.glob("P*.json"))]
    doctors = [_read_json(p) for p in sorted(core.glob("D*.json"))]
    patients.sort(key=lambda p: p.get("patient_id", ""))
    doctors.sort(key=lambda d: d.get("doctor_id", ""))
    return patients, doctors


def load_distractors(ids: list[str], world_dir: Path = WORLD_DIR) -> list[dict]:
    """Distractor patient files world_dir/distractors/<id>.json, in the order given."""
    folder = Path(world_dir) / "distractors"
    return [_read_json(folder / f"{i}.json") for i in ids]


def load_level(level: str, world_dir: Path = WORLD_DIR) -> dict:
    """Core patients and doctors plus the level's distractor patients.

    Without levels.json only "L1" (core only) exists; any other level raises FileNotFoundError.
    """
    world_dir = Path(world_dir)
    patients, doctors = load_core(world_dir=world_dir)
    levels_path = world_dir / "levels.json"
    if levels_path.exists():
        levels = _read_json(levels_path)
        if level not in levels:
            raise KeyError(f"level {level!r} is not defined in {levels_path}")
        distractor_ids = levels[level]["distractors"]
    elif level == "L1":
        distractor_ids = []
    else:
        raise FileNotFoundError(
            f"{levels_path} is missing, so only L1 (core only) is available, not {level!r}"
        )
    return {
        "patients": patients + load_distractors(distractor_ids, world_dir=world_dir),
        "doctors": doctors,
        "level": level,
    }


def all_memories(world: dict) -> list[dict]:
    """Flat list of every memory (as copies) with owner_id and owner_kind ('doctor' or 'patient') added."""
    out = []
    for d in world["doctors"]:
        out += [{**m, "owner_id": d["doctor_id"], "owner_kind": "doctor"} for m in d["memories"]]
    for p in world["patients"]:
        out += [{**m, "owner_id": p["patient_id"], "owner_kind": "patient"} for m in p["memories"]]
    return out
