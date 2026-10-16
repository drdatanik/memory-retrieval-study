"""Tests for src/render.py and the world loaders, against the mini fixture (tests/fixtures/mini_world)."""

import copy
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import render, world  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_world"
MARKER_RE = re.compile(r"<!-- m:(\S+) -->")

EXPECTED_P01 = "\n".join([
    "# Sunita Rao (P01)",
    "58 F · Primary doctor: Dr Anjali Mehta (D01) · Conditions: Type 2 diabetes, Hypertension",
    "",
    "## 2025-04-08 · Visit note · Dr Anjali Mehta (D01) <!-- m:P01-m01 -->",
    "S: k/c/o T2DM x 6 yrs, HTN. c/o burning feet on walking; no hypoglycaemic symptoms. O: BP 138/88. O/E: monofilament sensation reduced at both great toes. A: T2DM, HbA1c 7.4%, suboptimal. HTN, near target. P: 1. Metformin (Glycomet) 500 mg BD. 2. Telmisartan (Telma) 40 mg OD. 3. Repeat HbA1c in 3 months. 4. Annual eye exam due. Adv: 30 min brisk walk 5 days a week; cut sugar in tea. R/V in 3 months.",
    "",
    "## 2025-04-08 · Lab result · Lab <!-- m:P01-m02 -->",
    "Lab report, sample drawn 2025-04-08.",
    "HbA1c: 7.4 % (ref < 5.7)",
    "Fasting glucose: 142 mg/dL (ref 70-99)",
    "Creatinine: 0.9 mg/dL (ref 0.6-1.1)",
    "",
    "## 2025-06-12 · Allergy · Dr Anjali Mehta (D01) <!-- m:P01-m03 -->",
    "Penicillin: rash (2019). Confirmed at intake review. Avoid penicillin-class antibiotics.",
    "",
    "## 2025-09-02 · Prescription · Dr Anjali Mehta (D01) <!-- m:P01-m04 -->",
    "Rx: Telmisartan (Telma) 80 mg OD, increased from 40 mg OD for BP 146/92 at review on 2025-08-28. Metformin (Glycomet) 500 mg BD continued. Dispense 90 days.",
    "",
    "## 2026-02-10 · Patient-reported · Patient <!-- m:P01-m05 -->",
    "App message from patient, typed: legs feel tired after the walks, but the sugar readings on my meter are mostly under 140 now.",
    "",
    "## 2026-03-05 · Appointment · Front desk <!-- m:P01-m06 -->",
    "Appointment booked by phone for 2026-10-20 at 10:30 with Dr Anjali Mehta (D01), diabetes review.",
]) + "\n"

EXPECTED_D01 = "\n".join([
    "# Dr Anjali Mehta (D01): Internal Medicine and Diabetology",
    "",
    "## 2025-04-01 · Doctor preference · Dr Anjali Mehta (D01) <!-- m:D01-m01 -->",
    "Dr Mehta's notes always follow the S/O/A/P headings, written as S:, O:, A: and P:. The Plan is a numbered list, with one action per line.",
    "",
    "## 2025-04-03 · Doctor preference · Dr Anjali Mehta (D01) <!-- m:D01-m02 -->",
    "Telmisartan is Dr Mehta's first-line antihypertensive for patients with diabetes. Other agents are added only when blood pressure stays above target on telmisartan.",
]) + "\n"

EXPECTED_INDEX = "\n".join([
    "# Record index: Sunrise Family Clinic",
    "- doctors/D01-anjali-mehta.md: Dr Anjali Mehta (D01), Internal Medicine and Diabetology; documentation preferences",
    "- patients/P02-anil-kumar.md: Anil Kumar (P02), 71 M, primary D01; Atrial fibrillation, Hypertension",
    "- patients/P01-sunita-rao.md: Sunita Rao (P01), 58 F, primary D01; Type 2 diabetes, Hypertension",
]) + "\n"


@pytest.fixture(scope="module")
def mini():
    return world.load_level("L1", world_dir=FIXTURE)


def _memory(w, mid):
    return next(m for m in world.all_memories(w) if m["memory_id"] == mid)


# ---- slugs and paths --------------------------------------------------------

def test_slug_examples():
    assert render.slug("Sunita Rao") == "sunita-rao"
    assert render.slug("Dr Anjali Mehta") == "anjali-mehta"
    assert render.slug("Drew Smith") == "drew-smith"  # "Dr" alone is not a prefix to strip
    assert render.slug("  Dr  O'Brien-Smith ") == "o-brien-smith"
    assert render.slug("José Núñez") == "jose-nunez"


def test_paths(mini):
    p01 = next(p for p in mini["patients"] if p["patient_id"] == "P01")
    d01 = mini["doctors"][0]
    assert render.patient_path(p01) == "patients/P01-sunita-rao.md"
    assert render.doctor_path(d01) == "doctors/D01-anjali-mehta.md"


# ---- Markdown files and index -----------------------------------------------

def test_patient_file_golden(mini):
    assert render.render_md(mini)["patients/P01-sunita-rao.md"] == EXPECTED_P01


def test_doctor_file_golden(mini):
    assert render.render_md(mini)["doctors/D01-anjali-mehta.md"] == EXPECTED_D01


def test_index_golden_and_order(mini):
    # P02 (Anil Kumar) sorts before P01 (Sunita Rao) by slug, even though its ID is higher.
    assert render.render_index(mini) == EXPECTED_INDEX
    assert render.render_md(mini)["INDEX.md"] == EXPECTED_INDEX


def test_render_md_keys_in_file_order(mini):
    assert list(render.render_md(mini)) == [
        "INDEX.md",
        "doctors/D01-anjali-mehta.md",
        "patients/P02-anil-kumar.md",
        "patients/P01-sunita-rao.md",
    ]


def test_memories_sorted_by_date_then_id(mini):
    shuffled = copy.deepcopy(mini)
    for owner in shuffled["patients"] + shuffled["doctors"]:
        owner["memories"].reverse()
    assert render.render_md(shuffled) == render.render_md(mini)


# ---- CTX store --------------------------------------------------------------

def test_ctx_wrapper_and_file_order(mini):
    ctx = render.render_ctx(mini)
    assert ctx.startswith('<memory_store>\n<file path="doctors/D01-anjali-mehta.md">\n# Dr Anjali Mehta (D01)')
    assert ctx.endswith("</file>\n</memory_store>\n")
    paths = [line[len('<file path="'):-len('">')] for line in ctx.splitlines() if line.startswith("<file ")]
    assert paths == ["doctors/D01-anjali-mehta.md", "patients/P02-anil-kumar.md", "patients/P01-sunita-rao.md"]


def test_ctx_contains_every_markdown_file_verbatim(mini):
    ctx = render.render_ctx(mini)
    for path, content in render.render_md(mini).items():
        if path == "INDEX.md":
            continue
        assert f'<file path="{path}">\n{content}</file>\n' in ctx


def test_ctx_has_each_memory_marker_once(mini):
    markers = MARKER_RE.findall(render.render_ctx(mini))
    assert sorted(markers) == sorted(m["memory_id"] for m in world.all_memories(mini))


# ---- embedding units --------------------------------------------------------

def test_units_header_format(mini):
    units = {u["memory_id"]: u for u in render.render_units(mini)}
    assert len(units) == 12

    u = units["P01-m01"]
    assert u["text"] == (
        "[P01-m01] 2025-04-08 · Visit note · Patient: Sunita Rao (P01, 58 F) · Author: Dr Anjali Mehta (D01)\n"
        + _memory(mini, "P01-m01")["text"]
    )
    assert set(u) == {"memory_id", "owner_id", "patient_id", "doctor_id", "date", "type", "text"}
    assert (u["owner_id"], u["patient_id"], u["doctor_id"]) == ("P01", "P01", "D01")
    assert (u["date"], u["type"]) == ("2025-04-08", "visit_note")

    d = units["D01-m01"]
    assert d["text"] == (
        "[D01-m01] 2025-04-01 · Doctor preference · Doctor: Dr Anjali Mehta (D01)\n"
        + _memory(mini, "D01-m01")["text"]
    )
    assert (d["owner_id"], d["patient_id"], d["doctor_id"]) == ("D01", None, "D01")


# ---- ORACLE subset ----------------------------------------------------------

def test_render_memories_md_contains_only_requested(mini):
    wanted = [_memory(mini, "P02-m04"), _memory(mini, "D01-m02"), _memory(mini, "P01-m02")]
    out = render.render_memories_md(wanted, mini)

    # Owners in file order (doctors, then patients by name), memories in date order.
    assert MARKER_RE.findall(out) == ["D01-m02", "P02-m04", "P01-m02"]
    assert out.startswith(
        '<memory_store>\n<file path="doctors/D01-anjali-mehta.md">\n'
        "# Dr Anjali Mehta (D01): Internal Medicine and Diabetology\n\n"
        "## 2025-04-03 · Doctor preference · Dr Anjali Mehta (D01) <!-- m:D01-m02 -->\n"
    )
    assert '<file path="patients/P02-anil-kumar.md">\n# Anil Kumar (P02)\n' in out
    assert "71 M · Primary doctor: Dr Anjali Mehta (D01) · Conditions: Atrial fibrillation, Hypertension\n" in out
    assert "# Sunita Rao (P01)\n58 F · Primary doctor: Dr Anjali Mehta (D01) · Conditions: Type 2 diabetes, Hypertension\n" in out
    assert out.endswith("</file>\n</memory_store>\n")

    # Nothing else from the store leaks in.
    assert "P01-m01" not in out and "Penicillin" not in out and "D01-m01" not in out
    # Each section is the same text as in the full store.
    ctx = render.render_ctx(mini)
    for m in wanted:
        assert m["text"] in out and m["text"] in ctx


def test_render_memories_md_with_every_memory_equals_ctx(mini):
    assert render.render_memories_md(world.all_memories(mini), mini) == render.render_ctx(mini)


def test_render_memories_md_empty(mini):
    assert render.render_memories_md([], mini) == "<memory_store>\n</memory_store>\n"


def test_render_memories_md_rejects_unknown_id(mini):
    stray = {"memory_id": "P09-m01", "date": "2025-01-01", "type": "visit_note", "author": "D01", "text": "x"}
    with pytest.raises(ValueError, match="P09-m01"):
        render.render_memories_md([stray], mini)


def test_unknown_type_raises_with_memory_id(mini):
    bad = copy.deepcopy(mini)
    p02 = next(p for p in bad["patients"] if p["patient_id"] == "P02")
    p02["memories"][0]["type"] = "consult_note"
    with pytest.raises(ValueError, match="P02-m01"):
        render.render_ctx(bad)


# ---- loaders ----------------------------------------------------------------

def test_load_core_sorted_by_id():
    patients, doctors = world.load_core(world_dir=FIXTURE)
    assert [p["patient_id"] for p in patients] == ["P01", "P02"]
    assert [d["doctor_id"] for d in doctors] == ["D01"]


def test_load_level_l1_is_core_without_levels_json(mini):
    assert mini["level"] == "L1"
    assert [p["patient_id"] for p in mini["patients"]] == ["P01", "P02"]
    assert [d["doctor_id"] for d in mini["doctors"]] == ["D01"]


def test_levels_other_than_l1_need_levels_json():
    with pytest.raises(FileNotFoundError):
        world.load_level("L2", world_dir=FIXTURE)


def test_levels_json_adds_nested_distractors(tmp_path):
    shutil.copytree(FIXTURE / "core", tmp_path / "core")
    (tmp_path / "distractors").mkdir()
    extra = {
        "patient_id": "X0001", "name": "Test Patient", "age": 40, "sex": "M",
        "primary_doctor": "D01", "conditions": ["Asthma"],
        "memories": [{"memory_id": "X0001-m01", "date": "2025-06-01", "type": "visit_note",
                      "author": "D01", "text": "S: wheeze at night. A: asthma. P: salbutamol PRN."}],
    }
    (tmp_path / "distractors" / "X0001.json").write_text(json.dumps(extra), encoding="utf-8")
    (tmp_path / "levels.json").write_text(json.dumps({
        "L1": {"distractors": [], "ctx_tokens": 0},
        "L2": {"distractors": ["X0001"], "ctx_tokens": 0},
    }), encoding="utf-8")

    l1 = world.load_level("L1", world_dir=tmp_path)
    l2 = world.load_level("L2", world_dir=tmp_path)
    assert [p["patient_id"] for p in l1["patients"]] == ["P01", "P02"]
    assert [p["patient_id"] for p in l2["patients"]] == ["P01", "P02", "X0001"]
    assert [d["doctor_id"] for d in l2["doctors"]] == ["D01"]


def test_all_memories_adds_owner_keys_without_touching_world(mini):
    mems = world.all_memories(mini)
    assert len(mems) == 12
    p = next(m for m in mems if m["memory_id"] == "P01-m01")
    assert (p["owner_id"], p["owner_kind"]) == ("P01", "patient")
    d = next(m for m in mems if m["memory_id"] == "D01-m02")
    assert (d["owner_id"], d["owner_kind"]) == ("D01", "doctor")
    assert "owner_id" not in mini["patients"][0]["memories"][0]
