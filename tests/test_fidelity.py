"""Tests for src/fidelity.py. Bad worlds are built in memory from the mini fixture (tests/fixtures/mini_world)."""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import fidelity, render, world  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_world"


def _mini():
    return world.load_level("L1", world_dir=FIXTURE)


def _find(w, mid):
    for owner in w["doctors"] + w["patients"]:
        for m in owner["memories"]:
            if m["memory_id"] == mid:
                return m
    raise KeyError(mid)


def _patient(w, pid):
    return next(p for p in w["patients"] if p["patient_id"] == pid)


def test_mini_world_is_clean():
    assert fidelity.check(_mini()) == []


def test_appointment_with_future_text_date_is_allowed():
    w = _mini()
    appt = _find(w, "P01-m06")
    assert appt["type"] == "appointment" and "2026-10-20" in appt["text"]
    assert fidelity.check(w) == []


# ---- duplicated memory IDs --------------------------------------------------

def test_duplicate_id_within_one_patient_is_caught():
    w = _mini()
    p01 = _patient(w, "P01")
    p01["memories"].append(copy.deepcopy(p01["memories"][0]))  # second P01-m01
    problems = fidelity.check(w)
    assert any("P01-m01" in p and "appears 2 times" in p for p in problems)


def test_duplicate_id_across_patients_is_caught():
    w = _mini()
    _patient(w, "P02")["memories"].append(copy.deepcopy(_find(w, "P01-m01")))
    problems = fidelity.check(w)
    assert any("P01-m01" in p for p in problems)


# ---- dates ------------------------------------------------------------------

@pytest.mark.parametrize("bad_date", ["2025-02-30", "2026-10-12", "2025-03-31", "2025-4-8", "08/04/2025"])
def test_bad_date_is_caught(bad_date):
    w = _mini()
    _find(w, "P02-m02")["date"] = bad_date
    problems = fidelity.check(w)
    assert any("P02-m02" in p for p in problems), problems


# ---- types, authors, IDs, owners --------------------------------------------

def test_unknown_type_is_caught():
    w = _mini()
    _find(w, "P02-m01")["type"] = "consult_note"
    assert any("P02-m01" in p for p in fidelity.check(w))


def test_unknown_author_is_caught():
    w = _mini()
    _find(w, "P02-m03")["author"] = "D09"
    assert any("P02-m03" in p for p in fidelity.check(w))


def test_bad_memory_id_format_is_caught():
    w = _mini()
    _find(w, "P02-m02")["memory_id"] = "P02-m2"
    assert any("P02-m2" in p for p in fidelity.check(w))


def test_duplicate_patient_id_is_caught():
    w = _mini()
    clone = copy.deepcopy(_patient(w, "P02"))
    clone["name"] = "Someone Else"
    w["patients"].append(clone)
    problems = fidelity.check(w)
    assert any("duplicate patient ID" in p and "P02" in p for p in problems), problems


def test_unknown_primary_doctor_is_caught():
    w = _mini()
    _patient(w, "P01")["primary_doctor"] = "D07"
    assert any("P01" in p and "D07" in p for p in fidelity.check(w))


# ---- the three renderings must agree ----------------------------------------

def test_text_mismatch_in_ctx_is_caught(monkeypatch):
    w = _mini()
    original = render.render_ctx
    monkeypatch.setattr(render, "render_ctx", lambda world_: original(world_).replace(
        "Penicillin: rash", "Penicillin: hives"))
    problems = fidelity.check(w)
    assert any("P01-m03" in p and "render_ctx" in p for p in problems), problems


def test_missing_unit_is_caught(monkeypatch):
    w = _mini()
    original = render.render_units
    monkeypatch.setattr(render, "render_units", lambda world_: [
        u for u in original(world_) if u["memory_id"] != "P02-m01"])
    problems = fidelity.check(w)
    assert any("P02-m01" in p and "render_units" in p for p in problems), problems


# ---- command line -----------------------------------------------------------

def test_cli_ok(capsys):
    assert fidelity.main(["--level", "L1", "--world-dir", str(FIXTURE)]) == 0
    assert capsys.readouterr().out.strip() == "OK: 12 memories, all renderings consistent"


def test_cli_reports_problem_and_exits_1(tmp_path, capsys):
    dst = tmp_path / "mini_world"
    shutil.copytree(FIXTURE, dst)
    path = dst / "core" / "P02.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["memories"][1]["date"] = "2025-02-30"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    assert fidelity.main(["--level", "L1", "--world-dir", str(dst)]) == 1
    out = capsys.readouterr().out
    assert "P02-m02" in out and "OK:" not in out


def test_cli_level_without_levels_json_exits_2(capsys):
    assert fidelity.main(["--level", "L2", "--world-dir", str(FIXTURE)]) == 2
    assert "error" in capsys.readouterr().err
