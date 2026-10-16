"""Tests for src/embed_index.py.

Semantic tests use the real model (BAAI/bge-base-en-v1.5 via fastembed; first use downloads it).
Cache and batching tests use a fake model, so they need no download.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from src import embed_index
from src.embed_index import EmbeddingStore


def _unit(memory_id: str, patient_id: str | None, text: str) -> dict:
    return {"memory_id": memory_id, "patient_id": patient_id, "text": text}


# Six hand-written units: two patients and one doctor unit (doctor units have no patient_id).
UNITS = [
    _unit(
        "P01-m01",
        "P01",
        "[P01-m01] 2026-06-02 · Lab result · Patient: Sunita Rao (P01, 58 F) · Author: Lab\n"
        "HbA1c (glycated haemoglobin) 8.1 % (reference below 5.7 %). Fasting glucose 162 mg/dL (70-100).",
    ),
    _unit(
        "P01-m02",
        "P01",
        "[P01-m02] 2026-04-14 · Prescription · Patient: Sunita Rao (P01, 58 F) · Author: Dr Anjali Mehta (D01)\n"
        "Rx: Tab Metformin 500 mg BD after meals. Tab Amlodipine 5 mg OD.",
    ),
    _unit(
        "P01-m03",
        "P01",
        "[P01-m03] 2025-09-19 · Allergy · Patient: Sunita Rao (P01, 58 F) · Author: Dr Anjali Mehta (D01)\n"
        "Allergy: penicillin. Reaction: generalised rash with itching (2019).",
    ),
    _unit(
        "P02-m01",
        "P02",
        "[P02-m01] 2026-05-11 · Investigation · Patient: Ravi Menon (P02, 64 M) · Author: Dr Anjali Mehta (D01)\n"
        "X-ray right knee, AP and lateral: mild medial joint space narrowing, consistent with osteoarthritis.",
    ),
    _unit(
        "P02-m02",
        "P02",
        "[P02-m02] 2026-05-11 · Lab result · Patient: Ravi Menon (P02, 64 M) · Author: Lab\n"
        "Serum creatinine 1.1 mg/dL (0.7-1.3). eGFR 68 mL/min/1.73m2.",
    ),
    _unit(
        "D01-m01",
        None,
        "[D01-m01] 2025-04-01 · Doctor preference · Doctor: Dr Anjali Mehta (D01)\n"
        "Prefers HbA1c trends as a dated table, not prose.",
    ),
]


# ---------------------------------------------------------------- real model


@pytest.fixture(scope="module")
def real_store(tmp_path_factory):
    store = EmbeddingStore(cache_dir=tmp_path_factory.mktemp("emb_real"))
    store.build(UNITS)
    return store


def test_lay_query_ranks_hba1c_above_unrelated_units(real_store):
    hits = real_store.search("sugar levels", k=len(UNITS))
    ranks = {hit["memory_id"]: i for i, hit in enumerate(hits)}
    assert ranks["P01-m01"] < ranks["P02-m01"]  # knee X-ray
    assert ranks["P01-m01"] < ranks["P01-m03"]  # penicillin allergy
    assert ranks["P01-m01"] < ranks["P02-m02"]  # creatinine


def test_hits_are_sorted_by_score_and_carry_unit_text(real_store):
    hits = real_store.search("sugar levels", k=4)
    assert len(hits) == 4
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)
    texts = {unit["memory_id"]: unit["text"] for unit in UNITS}
    for hit in hits:
        assert hit["text"] == texts[hit["memory_id"]]
        assert set(hit) == {"memory_id", "score", "text"}


def test_patient_filter_keeps_only_that_patient_and_drops_doctor_units(real_store):
    hits = real_store.search("HbA1c", k=10, patient_id="P02")
    assert {hit["memory_id"] for hit in hits} == {"P02-m01", "P02-m02"}

    hits = real_store.search("HbA1c", k=10, patient_id="P01")
    ids = {hit["memory_id"] for hit in hits}
    assert ids == {"P01-m01", "P01-m02", "P01-m03"}
    assert "D01-m01" not in ids


def test_allowed_ids_restricts_candidates(real_store):
    allowed = {"P02-m01", "P02-m02", "D01-m01"}
    hits = real_store.search("sugar levels", k=10, allowed_ids=allowed)
    assert {hit["memory_id"] for hit in hits} == allowed
    assert "P01-m01" not in {hit["memory_id"] for hit in hits}


def test_allowed_ids_and_patient_filter_combine(real_store):
    hits = real_store.search("sugar levels", k=10, patient_id="P02", allowed_ids={"P01-m01", "P02-m02"})
    assert [hit["memory_id"] for hit in hits] == ["P02-m02"]


def test_empty_allowed_ids_returns_nothing(real_store):
    assert real_store.search("sugar levels", k=5, allowed_ids=set()) == []


# ---------------------------------------------------------------- fake model


class FakeModel:
    """Deterministic vectors that depend only on the text, so a text is its own nearest neighbour."""

    DIM = 12

    def __init__(self):
        self.embed_batches: list[int] = []

    def embed(self, texts):
        texts = list(texts)
        self.embed_batches.append(len(texts))
        for text in texts:
            yield _fake_vector(text)

    def query_embed(self, query):
        yield _fake_vector(query)

    @property
    def total_embedded(self) -> int:
        return sum(self.embed_batches)


def _fake_vector(text: str) -> np.ndarray:
    seed = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)
    return np.random.default_rng(seed).standard_normal(FakeModel.DIM).astype(np.float32)


@pytest.fixture
def fake_model(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(embed_index, "_get_model", lambda model_name: model)
    return model


def _synthetic_units(n: int) -> list[dict]:
    return [_unit(f"P{i // 10 + 1:02d}-m{i % 10 + 1:02d}", f"P{i // 10 + 1:02d}", f"record number {i}") for i in range(n)]


def test_build_embeds_in_batches_of_64(fake_model, tmp_path):
    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    store.build(_synthetic_units(130))
    assert fake_model.embed_batches == [64, 64, 2]
    assert len(store) == 130


def test_search_scores_a_text_against_itself_as_one(fake_model, tmp_path):
    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    store.build(UNITS)
    hits = store.search(UNITS[2]["text"], k=1)
    assert hits[0]["memory_id"] == "P01-m03"
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-5)


def test_reload_from_cache_does_not_reembed(fake_model, tmp_path):
    first = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    first.build(UNITS)
    assert fake_model.total_embedded == len(UNITS)
    before = first.search("sugar levels", k=3)

    reloaded = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    assert len(reloaded) == len(UNITS)
    embedded_before_rebuild = fake_model.total_embedded
    reloaded.build(UNITS)
    assert fake_model.total_embedded == embedded_before_rebuild  # nothing re-embedded

    after = reloaded.search("sugar levels", k=3)
    assert [h["memory_id"] for h in after] == [h["memory_id"] for h in before]
    assert [h["score"] for h in after] == pytest.approx([h["score"] for h in before])


def test_only_changed_and_new_units_are_embedded(fake_model, tmp_path):
    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    store.build(UNITS)
    assert fake_model.total_embedded == 6

    changed = [dict(u) for u in UNITS]
    changed[1]["text"] = changed[1]["text"] + "\nRx: Metformin dose increased to 1000 mg BD."
    new_unit = _unit("P03-m01", "P03", "[P03-m01] 2026-01-05 · Visit note\nNew patient, well.")
    store.build(changed + [new_unit])

    assert fake_model.embed_batches == [6, 2]  # one changed unit and one new unit
    assert len(store) == 7
    hits = store.search("x", k=10, allowed_ids={"P01-m02"})
    assert len(hits) == 1
    assert hits[0]["text"] == changed[1]["text"]  # the replaced row, not the old text


def test_changed_unit_survives_reload(fake_model, tmp_path):
    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    store.build(UNITS)
    changed = [dict(u) for u in UNITS]
    changed[0]["text"] = "[P01-m01] revised record text"
    store.build(changed)

    reloaded = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    assert len(reloaded) == len(UNITS)  # the old row was replaced, not duplicated
    before = fake_model.total_embedded
    reloaded.build(changed)
    assert fake_model.total_embedded == before
    hits = reloaded.search("x", k=10, allowed_ids={"P01-m01"})
    assert hits[0]["text"] == "[P01-m01] revised record text"


def test_subset_build_keeps_other_units_cached(fake_model, tmp_path):
    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    store.build(UNITS)
    store.build(UNITS[:2])  # a smaller store level
    assert fake_model.total_embedded == 6
    assert len(store) == 6
    hits = store.search("x", k=10, allowed_ids={"D01-m01"})
    assert [h["memory_id"] for h in hits] == ["D01-m01"]


def test_empty_build_and_empty_search_do_not_load_the_model(monkeypatch, tmp_path):
    def fail(model_name):
        raise AssertionError("model should not be loaded")

    monkeypatch.setattr(embed_index, "_get_model", fail)
    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    store.build([])
    assert len(store) == 0
    assert store.search("anything", k=5) == []


def test_duplicate_memory_ids_are_rejected(fake_model, tmp_path):
    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    with pytest.raises(ValueError, match="duplicate memory_id"):
        store.build([UNITS[0], dict(UNITS[0], text="other text")])


def test_cache_from_another_model_is_ignored(fake_model, tmp_path):
    EmbeddingStore(cache_dir=tmp_path, model_name="model-a").build(UNITS)
    other = EmbeddingStore(cache_dir=tmp_path, model_name="model-b")
    assert len(other) == 0
    other.build(UNITS)
    assert fake_model.total_embedded == 12


def test_corrupt_cache_starts_empty_and_rebuilds(fake_model, tmp_path):
    EmbeddingStore(cache_dir=tmp_path, model_name="fake").build(UNITS)
    (tmp_path / "ids.json").write_text("not json at all", encoding="utf-8")

    store = EmbeddingStore(cache_dir=tmp_path, model_name="fake")
    assert len(store) == 0
    store.build(UNITS)
    assert fake_model.total_embedded == 12


def test_cache_files_have_the_documented_shape(fake_model, tmp_path):
    EmbeddingStore(cache_dir=tmp_path, model_name="fake").build(UNITS)
    vectors = np.load(tmp_path / "vectors.npy")
    assert vectors.shape == (6, FakeModel.DIM)
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    ids = json.loads((tmp_path / "ids.json").read_text(encoding="utf-8"))
    assert [memory_id for memory_id, _ in ids] == [u["memory_id"] for u in UNITS]
    assert all(len(sha1) == 40 for _, sha1 in ids)
