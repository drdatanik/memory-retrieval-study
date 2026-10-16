"""Embedding index for the EMB arm: one vector per memory unit, exact cosine search.

The index keeps every unit it has been given, so one cache serves every store level.
`search` restricts the candidates with `allowed_ids` (the current level) and `patient_id`.

Cache files in `cache_dir` (aligned row by row):
  vectors.npy  float32 (n, d), L2-normalised
  ids.json     [[memory_id, sha1_of_text], ...]
  units.json   {"model": model_name, "units": [{"patient_id", "text"}, ...]}
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import numpy as np

from src import config

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = ROOT / "runs" / "emb_cache"
EMBED_BATCH_SIZE = 64

_MODEL_LOCK = threading.Lock()  # guards the model cache
_INFER_LOCK = threading.Lock()  # serialises calls into one model
_MODELS: dict[str, object] = {}


def _get_model(model_name: str):
    """Return a fastembed model, shared across stores and loaded on first use."""
    with _MODEL_LOCK:
        model = _MODELS.get(model_name)
        if model is None:
            from fastembed import TextEmbedding

            model = TextEmbedding(model_name=model_name)
            _MODELS[model_name] = model
        return model


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _l2_normalise(vectors) -> np.ndarray:
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


class EmbeddingStore:
    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        model_name: str = config.EMBED_MODEL,
    ):
        self.cache_dir = Path(cache_dir)
        self.model_name = model_name
        self._lock = threading.Lock()  # guards the swap of the index state
        self._rows: list[dict] = []  # {"memory_id", "sha1", "patient_id", "text"}
        self._vectors: np.ndarray | None = None  # aligned with _rows
        self._load()

    def __len__(self) -> int:
        return len(self._rows)

    # ----- building -------------------------------------------------------

    def build(self, units: list[dict]) -> None:
        """Make sure every unit in `units` has a current vector.

        Units already cached under the same (memory_id, sha1 of text) are reused. Anything
        new or changed is embedded in batches of 64. Cached units that are not in `units`
        are kept, so other store levels stay cached. A changed unit replaces its old row.
        """
        incoming: dict[str, dict] = {}
        for unit in units:
            memory_id = unit["memory_id"]
            if memory_id in incoming:
                raise ValueError(f"duplicate memory_id in build(): {memory_id}")
            incoming[memory_id] = {
                "memory_id": memory_id,
                "sha1": _sha1(unit["text"]),
                "patient_id": unit.get("patient_id"),
                "text": unit["text"],
            }

        with self._lock:
            rows, vectors = self._rows, self._vectors
        old_index = {row["memory_id"]: i for i, row in enumerate(rows)}

        final_rows: list[dict] = []
        reuse_from: list[int | None] = []  # index into old vectors, or None to embed
        for i, row in enumerate(rows):
            if row["memory_id"] not in incoming:
                final_rows.append(row)
                reuse_from.append(i)
        for memory_id, unit in incoming.items():
            i = old_index.get(memory_id)
            if i is not None and rows[i]["sha1"] == unit["sha1"]:
                final_rows.append(rows[i])
                reuse_from.append(i)
            else:
                final_rows.append(unit)
                reuse_from.append(None)

        to_embed = [j for j, src in enumerate(reuse_from) if src is None]
        if not to_embed:
            return  # every unit is already cached and nothing needs dropping

        fresh = self._embed_documents([final_rows[j]["text"] for j in to_embed])
        out = np.empty((len(final_rows), fresh.shape[1]), dtype=np.float32)
        fresh_pos = 0
        for j, src in enumerate(reuse_from):
            if src is None:
                out[j] = fresh[fresh_pos]
                fresh_pos += 1
            else:
                out[j] = vectors[src]

        with self._lock:
            self._rows = final_rows
            self._vectors = out
            self._save()

    def _embed_documents(self, texts: list[str]) -> np.ndarray:
        model = _get_model(self.model_name)
        out: list[np.ndarray] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            with _INFER_LOCK:
                out.extend(model.embed(batch))
        return _l2_normalise(np.asarray(out, dtype=np.float32))

    def _embed_query(self, query: str) -> np.ndarray:
        model = _get_model(self.model_name)
        with _INFER_LOCK:
            vector = next(iter(model.query_embed(query)))
        return _l2_normalise(vector)[0]

    # ----- searching ------------------------------------------------------

    def search(
        self,
        query: str,
        k: int = config.SEARCH_K,
        patient_id: str | None = None,
        allowed_ids: set[str] | None = None,
    ) -> list[dict]:
        """Exact cosine search. Returns [{"memory_id", "score", "text"}], highest score first.

        `allowed_ids` limits the candidates to those memory IDs. `patient_id` limits them to
        that patient's units; doctor units have no patient_id, so they are excluded.
        """
        with self._lock:
            rows, vectors = self._rows, self._vectors
        k = int(k)
        if vectors is None or k < 1:
            return []

        n = len(rows)
        keep = np.ones(n, dtype=bool)
        if allowed_ids is not None:
            keep &= np.fromiter((row["memory_id"] in allowed_ids for row in rows), dtype=bool, count=n)
        if patient_id is not None:
            keep &= np.fromiter((row["patient_id"] == patient_id for row in rows), dtype=bool, count=n)
        idx = np.flatnonzero(keep)
        if idx.size == 0:
            return []

        query_vector = self._embed_query(query)
        scores = vectors[idx] @ query_vector
        top = np.argsort(-scores, kind="stable")[:k]
        hits = []
        for j in top:
            row = rows[idx[j]]
            hits.append({"memory_id": row["memory_id"], "score": float(scores[j]), "text": row["text"]})
        return hits

    # ----- cache files ----------------------------------------------------

    def _load(self) -> None:
        paths = {name: self.cache_dir / name for name in ("vectors.npy", "ids.json", "units.json")}
        if not all(p.exists() for p in paths.values()):
            return
        try:
            vectors = np.load(paths["vectors.npy"], allow_pickle=False)
            ids = json.loads(paths["ids.json"].read_text(encoding="utf-8"))
            meta = json.loads(paths["units.json"].read_text(encoding="utf-8"))
            if meta.get("model") != self.model_name:
                return  # vectors from another model are not comparable
            units = meta["units"]
            if not (len(ids) == len(units) == len(vectors)):
                return
            rows = []
            for (memory_id, sha1), unit in zip(ids, units):
                if _sha1(unit["text"]) != sha1:
                    return
                rows.append(
                    {"memory_id": memory_id, "sha1": sha1, "patient_id": unit["patient_id"], "text": unit["text"]}
                )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return  # unreadable cache: start empty and rebuild
        if rows:
            self._rows = rows
            self._vectors = _l2_normalise(vectors)

    def _save(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self._vectors is not None:
            vectors = self._vectors
        else:
            vectors = np.zeros((0, 0), dtype=np.float32)
        tmp = self.cache_dir / "vectors.tmp.npy"
        np.save(tmp, vectors)
        os.replace(tmp, self.cache_dir / "vectors.npy")

        ids = [[row["memory_id"], row["sha1"]] for row in self._rows]
        meta = {
            "model": self.model_name,
            "units": [{"patient_id": row["patient_id"], "text": row["text"]} for row in self._rows],
        }
        for name, payload in (("ids.json", ids), ("units.json", meta)):
            tmp = self.cache_dir / (name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cache_dir / name)
