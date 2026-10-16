"""Checks that every memory renders identically across the stores the arms read.

    python -m src.fidelity --level L1

Each memory ID must appear exactly once in render_ctx, exactly once across the render_md files
(counting <!-- m:ID --> markers), and exactly once in render_units, with its text byte-identical
in each. Also checks ID format, dates (valid and inside the study window), types, authors and
duplicate owner IDs. Every problem string names the memory (or owner) it is about.
"""

import argparse
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from src import config, render
from src.world import WORLD_DIR, all_memories, load_level

ID_RE = re.compile(r"^[PXD]\d{2,4}-m\d{2}$")
PATIENT_ID_RE = re.compile(r"^[PX]\d{2,4}$")
DOCTOR_ID_RE = re.compile(r"^D\d{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MARKER_RE = re.compile(r"<!-- m:(\S+) -->")
PATIENT_KEYS = ("patient_id", "name", "age", "sex", "primary_doctor", "conditions", "memories")
DOCTOR_KEYS = ("doctor_id", "name", "specialty", "memories")
MEMORY_KEYS = ("memory_id", "date", "type", "author", "text")
WINDOW_START = date.fromisoformat(config.WINDOW_START)
WINDOW_END = date.fromisoformat(config.WINDOW_END)


def _matches(pattern: re.Pattern, value) -> bool:
    return isinstance(value, str) and pattern.match(value) is not None


def check(world: dict) -> list[str]:
    """Return the problems found in the world (an empty list means it is consistent)."""
    problems: list[str] = []
    blocked, duplicated = _check_data(world, problems)
    if not blocked:
        try:
            _check_renderings(world, duplicated, problems)
        except Exception as exc:  # the data checks should catch anything that breaks rendering
            problems.append(f"rendering failed: {type(exc).__name__}: {exc}")
    return list(dict.fromkeys(problems))


# ---- structural checks on the raw data --------------------------------------

def _check_data(world: dict, problems: list[str]) -> tuple[bool, set[str]]:
    """Returns (True if rendering would fail, set of memory IDs that occur more than once)."""
    blocked = False
    owners: list[tuple[str, str, dict]] = []  # (owner_id, kind, owner) for owners with all keys

    doctor_id_list = []
    for d in world.get("doctors", []):
        missing = [k for k in DOCTOR_KEYS if k not in d]
        if missing:
            problems.append(f"doctor {d.get('doctor_id', '?')}: missing keys {missing}")
            blocked = True
            continue
        did = d["doctor_id"]
        if not _matches(DOCTOR_ID_RE, did):
            problems.append(f"{did}: doctor ID does not match ^D\\d{{2}}$")
        doctor_id_list.append(did)
        owners.append((did, "doctor", d))
    doctor_ids = {did for did in doctor_id_list if isinstance(did, str)}
    for did, n in Counter(doctor_id_list).items():
        if n > 1:
            problems.append(f"{did}: duplicate doctor ID (appears {n} times)")

    patient_id_list = []
    for p in world.get("patients", []):
        missing = [k for k in PATIENT_KEYS if k not in p]
        if missing:
            problems.append(f"patient {p.get('patient_id', '?')}: missing keys {missing}")
            blocked = True
            continue
        pid = p["patient_id"]
        patient_id_list.append(pid)
        if not _matches(PATIENT_ID_RE, pid):
            problems.append(f"{pid}: patient ID does not match ^[PX]\\d{{2,4}}$")
        primary = p["primary_doctor"]
        if not (isinstance(primary, str) and primary in doctor_ids):
            problems.append(f"{pid}: primary doctor {primary!r} is not a doctor in this world")
            blocked = True
        owners.append((pid, "patient", p))
    for pid, n in Counter(patient_id_list).items():
        if n > 1:
            problems.append(f"{pid}: duplicate patient ID (appears {n} times)")

    memory_ids: list[str] = []
    for owner_id, _kind, owner in owners:
        for m in owner["memories"]:
            missing = [k for k in MEMORY_KEYS if k not in m]
            if missing:
                problems.append(f"{m.get('memory_id', '?')}: missing keys {missing}")
                blocked = True
                continue
            if _check_memory(m, owner_id, doctor_ids, problems):
                blocked = True
            if isinstance(m["memory_id"], str):
                memory_ids.append(m["memory_id"])

    counts = Counter(memory_ids)
    duplicated = {mid for mid, n in counts.items() if n > 1}
    for mid in sorted(duplicated):
        problems.append(f"{mid}: memory ID appears {counts[mid]} times in the world data; IDs must be unique")
    return blocked, duplicated


def _check_memory(m: dict, owner_id: str, doctor_ids: set[str], problems: list[str]) -> bool:
    """Per-memory checks. Returns True if rendering this memory would fail."""
    mid = m["memory_id"]
    blocked = False

    if not _matches(ID_RE, mid):
        problems.append(f"{mid}: memory ID does not match ^[PXD]\\d{{2,4}}-m\\d{{2}}$")
    if isinstance(mid, str) and mid.rsplit("-m", 1)[0] != owner_id:
        problems.append(f"{mid}: memory ID prefix does not match its owner {owner_id}")

    # Appointments follow the same window: the record date is the booking day. A future
    # appointment date lives in the text, which is not checked here.
    day = m["date"]
    if not _matches(DATE_RE, day):
        problems.append(f"{mid}: date {day!r} is not ISO YYYY-MM-DD")
    else:
        try:
            when = date.fromisoformat(day)
        except ValueError:
            problems.append(f"{mid}: date {day} is not a real calendar date")
        else:
            if not WINDOW_START <= when <= WINDOW_END:
                problems.append(
                    f"{mid}: date {day} is outside the window {config.WINDOW_START} to {config.WINDOW_END}"
                )

    if not (isinstance(m["type"], str) and m["type"] in render.TYPE_LABELS):
        problems.append(f"{mid}: unknown type {m['type']!r}")
        blocked = True

    author = m["author"]
    if not (isinstance(author, str) and (author in render.AUTHOR_LABELS or author in doctor_ids)):
        problems.append(f"{mid}: unknown author {author!r} (expected a doctor ID, 'patient', 'lab' or 'front_desk')")
        blocked = True

    if not isinstance(m["text"], str):
        problems.append(f"{mid}: text is not a string")
        blocked = True
    return blocked


# ---- consistency of the three renderings ------------------------------------

def _primary_doctors(world: dict) -> dict[str, str]:
    """owner_id -> the doctor ID its memories are rendered under (a doctor is their own)."""
    out = {d["doctor_id"]: d["doctor_id"] for d in world["doctors"]}
    out.update({p["patient_id"]: p["primary_doctor"] for p in world["patients"]})
    return out


def _body_after_marker(content: str, mid: str) -> str | None:
    """The text that follows the heading line carrying this memory's marker, or None if absent."""
    match = re.search(
        r"^## [^\n]*<!-- m:" + re.escape(mid) + r" -->\n", content, flags=re.MULTILINE
    )
    return content[match.end():] if match else None


def _check_renderings(world: dict, skip: set[str], problems: list[str]) -> None:
    """Compare render_ctx, render_md and render_units for every memory not already flagged as a duplicate."""
    memories = all_memories(world)
    by_id = {m["memory_id"]: m for m in memories}
    checked = [mid for mid in by_id if mid not in skip]

    ctx = render.render_ctx(world)
    md = render.render_md(world)
    units = render.render_units(world)
    md_text = "".join(md.values())
    if render.render_index(world) != md[render.INDEX_PATH]:
        problems.append("INDEX.md: render_index does not match render_md")

    counts = {
        "render_ctx": Counter(MARKER_RE.findall(ctx)),
        "render_md": Counter(MARKER_RE.findall(md_text)),
        "render_units": Counter(u["memory_id"] for u in units),
    }
    for label, c in counts.items():
        for mid in checked:
            if c[mid] != 1:
                problems.append(f"{mid}: appears {c[mid]} times in {label}, expected exactly once")
        for mid in c:
            if mid not in by_id:
                problems.append(f"{mid}: appears in {label} but is not a memory in this world")

    for mid in checked:
        text = by_id[mid]["text"]
        for label, content in (("render_ctx", ctx), ("render_md", md_text)):
            body = _body_after_marker(content, mid)
            if body is None or not body.startswith(text) or body[len(text):len(text) + 1] != "\n":
                problems.append(f"{mid}: text in {label} is not byte-identical to the world data")

    units_by_id: dict[str, dict] = {}
    for u in units:
        units_by_id.setdefault(u["memory_id"], u)
    primary = _primary_doctors(world)
    for mid in checked:
        m = by_id[mid]
        u = units_by_id.get(mid)
        if u is None:
            continue  # already reported by the count check
        header, sep, body = u["text"].partition("\n")
        if sep != "\n" or body != m["text"] or not header.startswith(f"[{mid}] "):
            problems.append(f"{mid}: text in render_units is not byte-identical to the world data")
        patient_id = m["owner_id"] if m["owner_kind"] == "patient" else None
        expected = (m["owner_id"], patient_id, primary[m["owner_id"]], m["date"], m["type"])
        actual = (u["owner_id"], u["patient_id"], u["doctor_id"], u["date"], u["type"])
        if actual != expected:
            problems.append(f"{mid}: render_units fields differ from the world data")


# ---- CLI --------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that every memory renders consistently.")
    parser.add_argument("--level", default="L1", help="store level to check (default L1, core only)")
    parser.add_argument("--world-dir", type=Path, default=WORLD_DIR, help="world folder (default ROOT/world)")
    args = parser.parse_args(argv)

    try:
        world = load_level(args.level, world_dir=args.world_dir)
    except (OSError, KeyError, ValueError) as exc:
        print(f"error: cannot load level {args.level}: {exc}", file=sys.stderr)
        return 2

    problems = check(world)
    if problems:
        for p in problems:
            print(p)
        print(f"{len(problems)} problem(s) in level {args.level}")
        return 1
    print(f"OK: {len(all_memories(world))} memories, all renderings consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
