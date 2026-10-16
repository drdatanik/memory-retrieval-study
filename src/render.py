"""Renderings of the clinic store. Every arm reads the store through these functions.

Formats follow SPEC section 1 (render.py). File order: doctors by ID, then patients by
(slug(name), patient_id). Memories: date order, then ID.
"""

import re
import unicodedata

CLINIC_NAME = "Sunrise Family Clinic"
INDEX_PATH = "INDEX.md"
DOCTOR_INDEX_SUFFIX = "documentation preferences"
NO_CONDITIONS = "none recorded"

TYPE_LABELS = {
    "visit_note": "Visit note",
    "prescription": "Prescription",
    "allergy": "Allergy",
    "lab_result": "Lab result",
    "investigation": "Investigation",
    "referral": "Referral",
    "referral_outcome": "Referral outcome",
    "patient_context": "Patient-reported",
    "doctor_preference": "Doctor preference",
    "appointment": "Appointment",
}
AUTHOR_LABELS = {"patient": "Patient", "lab": "Lab", "front_desk": "Front desk"}


def slug(name: str) -> str:
    """'Sunita Rao' -> 'sunita-rao'; 'Dr Anjali Mehta' -> 'anjali-mehta'."""
    s = name.strip()
    if s.startswith("Dr "):
        s = s[3:]
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def patient_path(p: dict) -> str:
    return f"patients/{p['patient_id']}-{slug(p['name'])}.md"


def doctor_path(d: dict) -> str:
    return f"doctors/{d['doctor_id']}-{slug(d['name'])}.md"


# ---- internal helpers -------------------------------------------------------

def _owners(world: dict) -> list[tuple[str, dict]]:
    """Owners in file order: doctors by ID, then patients by (slug(name), patient_id)."""
    doctors = sorted(world["doctors"], key=lambda d: d["doctor_id"])
    patients = sorted(world["patients"], key=lambda p: (slug(p["name"]), p["patient_id"]))
    return [("doctor", d) for d in doctors] + [("patient", p) for p in patients]


def _path(kind: str, owner: dict) -> str:
    return doctor_path(owner) if kind == "doctor" else patient_path(owner)


def _doctor_lookup(world: dict) -> dict[str, dict]:
    return {d["doctor_id"]: d for d in world["doctors"]}


def _memory_labels(m: dict, doctors: dict) -> tuple[str, str]:
    """(type label, author label) for a memory. Unknown values raise, naming the memory ID."""
    mid = m["memory_id"]
    if m["type"] not in TYPE_LABELS:
        raise ValueError(f"{mid}: unknown type {m['type']!r}")
    if m["author"] in AUTHOR_LABELS:
        author = AUTHOR_LABELS[m["author"]]
    elif m["author"] in doctors:
        author = f"{doctors[m['author']]['name']} ({m['author']})"
    else:
        raise ValueError(f"{mid}: unknown author {m['author']!r}")
    return TYPE_LABELS[m["type"]], author


def _sorted(memories: list[dict]) -> list[dict]:
    return sorted(memories, key=lambda m: (m["date"], m["memory_id"]))


def _conditions(owner: dict) -> str:
    return ", ".join(owner["conditions"]) or NO_CONDITIONS


def _section(m: dict, doctors: dict) -> str:
    type_label, author_label = _memory_labels(m, doctors)
    return f"## {m['date']} · {type_label} · {author_label} <!-- m:{m['memory_id']} -->\n{m['text']}\n"


def _file(kind: str, owner: dict, memories: list[dict], doctors: dict) -> str:
    """One owner's Markdown file, holding only the given memories (in date then ID order)."""
    if kind == "doctor":
        head = f"# {owner['name']} ({owner['doctor_id']}): {owner['specialty']}\n"
    else:
        doc = doctors.get(owner["primary_doctor"])
        if doc is None:
            raise ValueError(f"{owner['patient_id']}: unknown primary doctor {owner['primary_doctor']!r}")
        head = (
            f"# {owner['name']} ({owner['patient_id']})\n"
            f"{owner['age']} {owner['sex']} · Primary doctor: {doc['name']} ({doc['doctor_id']})"
            f" · Conditions: {_conditions(owner)}\n"
        )
    return head + "".join("\n" + _section(m, doctors) for m in _sorted(memories))


def _index(world: dict) -> str:
    lines = [f"# Record index: {CLINIC_NAME}"]
    for kind, owner in _owners(world):
        if kind == "doctor":
            lines.append(
                f"- {doctor_path(owner)}: {owner['name']} ({owner['doctor_id']}), "
                f"{owner['specialty']}; {DOCTOR_INDEX_SUFFIX}"
            )
        else:
            lines.append(
                f"- {patient_path(owner)}: {owner['name']} ({owner['patient_id']}), "
                f"{owner['age']} {owner['sex']}, primary {owner['primary_doctor']}; {_conditions(owner)}"
            )
    return "\n".join(lines) + "\n"


def _files(world: dict) -> list[tuple[str, str]]:
    """(path, content) for every owner file, in file order. INDEX.md is not included."""
    doctors = _doctor_lookup(world)
    return [
        (_path(kind, owner), _file(kind, owner, owner["memories"], doctors))
        for kind, owner in _owners(world)
    ]


# ---- public API ---------------------------------------------------------------

def render_md(world: dict) -> dict[str, str]:
    """{relative_path: content} for INDEX.md and every doctor and patient file."""
    out = {INDEX_PATH: render_index(world)}
    out.update(_files(world))
    return out


def render_index(world: dict) -> str:
    """INDEX.md: one line per file."""
    return _index(world)


def render_ctx(world: dict) -> str:
    """The whole store for the CTX arm: every owner file wrapped in <memory_store> / <file path=...>."""
    parts = ["<memory_store>\n"]
    parts += [f'<file path="{path}">\n{content}</file>\n' for path, content in _files(world)]
    parts.append("</memory_store>\n")
    return "".join(parts)


def render_units(world: dict) -> list[dict]:
    """One embedding unit per memory, in file order. text = header line + newline + memory text."""
    doctors = _doctor_lookup(world)
    units = []
    for kind, owner in _owners(world):
        for m in _sorted(owner["memories"]):
            type_label, author_label = _memory_labels(m, doctors)
            if kind == "patient":
                owner_id = patient_id = owner["patient_id"]
                doctor_id = owner["primary_doctor"]
                header = (
                    f"[{m['memory_id']}] {m['date']} · {type_label} · Patient: "
                    f"{owner['name']} ({owner['patient_id']}, {owner['age']} {owner['sex']})"
                    f" · Author: {author_label}"
                )
            else:
                owner_id = doctor_id = owner["doctor_id"]
                patient_id = None
                header = (
                    f"[{m['memory_id']}] {m['date']} · {type_label} · "
                    f"Doctor: {owner['name']} ({owner['doctor_id']})"
                )
            units.append({
                "memory_id": m["memory_id"],
                "owner_id": owner_id,
                "patient_id": patient_id,
                "doctor_id": doctor_id,
                "date": m["date"],
                "type": m["type"],
                "text": f"{header}\n{m['text']}",
            })
    return units


def render_memories_md(memories: list[dict], world: dict) -> str:
    """The given memories only, as CTX-style sections (the ORACLE arm).

    Each owner's file heading and info line are the same as in the full file. Owners with no
    given memories are left out. Raises ValueError if a memory ID is not in the world.
    """
    doctors = _doctor_lookup(world)
    wanted: dict[str, dict] = {}
    for m in memories:
        wanted.setdefault(m["memory_id"], m)
    known: set[str] = set()
    parts = ["<memory_store>\n"]
    for kind, owner in _owners(world):
        known.update(m["memory_id"] for m in owner["memories"])
        chosen = [wanted[m["memory_id"]] for m in owner["memories"] if m["memory_id"] in wanted]
        if chosen:
            parts.append(f'<file path="{_path(kind, owner)}">\n{_file(kind, owner, chosen, doctors)}</file>\n')
    missing = sorted(set(wanted) - known)
    if missing:
        raise ValueError(f"memories not in this world: {', '.join(missing)}")
    parts.append("</memory_store>\n")
    return "".join(parts)
