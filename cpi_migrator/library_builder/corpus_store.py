#!/usr/bin/env python3
"""
corpus_store.py  --  persistent, editable, incrementally-updatable capability store.

The distillation pipeline (corpus_pipeline.build_corpus) is *expensive*: it walks
the whole raw corpus (potentially gigabytes / tens of thousands of files) and
extracts + dedupes capabilities. Doing that inside a UI request, every session,
is why the corpus "never worked" against the real harvest.

This module splits the expensive LEARN step from the cheap LOAD step:

    distill once  ->  save to <Corpus>/      (offline; CLI: library_builder.build_store)
    load          <-  read <Corpus>/         (instant; every session)
    update        +=  merge new capabilities (add-only, deduped by cap_id)

On disk the store is **sharded by type** -- one small JSON file per capability
type (groovy.json, xslt.json, mmap.json, ...) plus an index.json manifest. Per-
type shards keep each file small and individually movable/editable, and let an
incremental update rewrite only the shard(s) that actually gained entries. The
raw multi-GB corpus stays external; the store holds only the distilled, deduped
capability view (a few MB), which is exactly the "editable corpus" the user can
hand-tune.

The store reconstitutes NormalizedCapability objects on load, so the existing
solver (search / solve) and the generator's resource lookup query it unchanged.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from library_builder.solver import NormalizedCapability

INDEX_NAME = "index.json"
STORE_VERSION = 1


# ───────────────────────── serialization helpers ──────────────────────────
def _jsonable(obj):
    """Recursively convert a capability's `raw` payload into JSON-safe data:
    dataclass -> dict, set -> sorted list, leave scalars/list/dict, fall back
    to str() for anything exotic. Best-effort and lossless for the dataclass
    capability objects the catalogs produce."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_jsonable(v) for v in obj)
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return str(obj)


def _cap_to_dict(c: NormalizedCapability) -> dict:
    """NormalizedCapability -> compact JSON dict (keywords set -> sorted list,
    raw dataclass -> nested dict so the pattern stays reusable for ADAPT)."""
    return {
        "cap_id": c.cap_id,
        "ctype": c.ctype,
        "intent": c.intent,
        "keywords": sorted(c.keywords),
        "varies": _jsonable(c.varies),
        "when_to_use": c.when_to_use,
        "weight": c.weight,
        "needs_binding": c.needs_binding,
        "source_ref": c.source_ref,
        "raw": _jsonable(c.raw),
    }


def _cap_from_dict(d: dict) -> NormalizedCapability:
    """JSON dict -> NormalizedCapability. `raw` comes back as a plain dict
    (enough for search/solve and for the generator to read pattern detail);
    keywords is rehydrated to a set."""
    return NormalizedCapability(
        cap_id=d["cap_id"], ctype=d.get("ctype", ""),
        intent=d.get("intent", ""), keywords=set(d.get("keywords", [])),
        varies=d.get("varies", []), when_to_use=d.get("when_to_use", ""),
        weight=d.get("weight", 0), needs_binding=d.get("needs_binding", False),
        source_ref=d.get("source_ref", ""), raw=d.get("raw"))


# ─────────────────────────────── the store ────────────────────────────────
class CorpusStore:
    """A persisted, queryable capability library. Holds the normalized
    capability list grouped by type; saves/loads as sharded JSON; merges new
    capabilities add-only (deduped by cap_id)."""

    def __init__(self, caps: Optional[List[NormalizedCapability]] = None,
                 source: str = ""):
        self.caps: List[NormalizedCapability] = list(caps or [])
        self.source = source
        self._idf = {}

    # -- construction -------------------------------------------------------
    @classmethod
    def from_corpus(cls, corpus, source: str = "") -> "CorpusStore":
        """Wrap a freshly distilled Corpus (corpus_pipeline.build_corpus)."""
        return cls(caps=list(corpus.normalized), source=source)

    def by_type(self) -> dict:
        out: dict = {}
        for c in self.caps:
            out.setdefault(c.ctype, []).append(c)
        return out

    # -- persistence --------------------------------------------------------
    def save(self, root) -> dict:
        """Write one shard per type + an index.json manifest. Returns the
        manifest. Rewrites only the shards present in this store; per-type
        sharding means an incremental save touches just the changed types."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        grouped = self.by_type()
        shard_meta = {}
        for ctype, caps in grouped.items():
            shard = f"{ctype}.json"
            (root / shard).write_text(
                json.dumps([_cap_to_dict(c) for c in caps], indent=1,
                           ensure_ascii=False), encoding="utf-8")
            shard_meta[ctype] = {"file": shard, "count": len(caps)}
        index = {
            "store_version": STORE_VERSION,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": self.source,
            "total_capabilities": len(self.caps),
            "shards": shard_meta,
        }
        (root / INDEX_NAME).write_text(
            json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
        return index

    @classmethod
    def load(cls, root) -> "CorpusStore":
        """Read shards back into a queryable store -- the fast path. Does NOT
        touch the raw corpus. Tolerant of a missing index (reads every *.json
        shard it finds except the index itself)."""
        root = Path(root)
        if not root.exists():
            raise FileNotFoundError(f"corpus store not found: {root}")
        index_path = root / INDEX_NAME
        shard_files = []
        if index_path.exists():
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            shard_files = [root / m["file"]
                           for m in idx.get("shards", {}).values()]
            source = idx.get("source", "")
        else:
            shard_files = [p for p in root.glob("*.json") if p.name != INDEX_NAME]
            source = ""
        caps: List[NormalizedCapability] = []
        for sf in shard_files:
            if not sf.exists():
                continue
            for d in json.loads(sf.read_text(encoding="utf-8")):
                caps.append(_cap_from_dict(d))
        return cls(caps=caps, source=source)

    # -- incremental update -------------------------------------------------
    def merge_caps(self, new_caps: List[NormalizedCapability]) -> dict:
        """Add-only merge keyed on cap_id: append capabilities whose cap_id is
        not already present, skip exact duplicates. Returns {added, skipped}.
        This is the 'I found something new' path -- no full re-distillation."""
        have = {c.cap_id for c in self.caps}
        added = 0
        for c in new_caps:
            if c.cap_id in have:
                continue
            self.caps.append(c)
            have.add(c.cap_id)
            added += 1
        self._idf = {}   # invalidate cached idf; recomputed on next search
        return {"added": added, "skipped": len(new_caps) - added,
                "total": len(self.caps)}

    def update_from_corpus(self, corpus) -> dict:
        """Merge the capabilities of a freshly distilled (small) corpus."""
        return self.merge_caps(list(corpus.normalized))

    # -- query (same surface as Corpus) ------------------------------------
    def report(self) -> dict:
        by_type = {}
        for c in self.caps:
            by_type[c.ctype] = by_type.get(c.ctype, 0) + 1
        return {"capabilities": len(self.caps), "by_type": by_type,
                "source": self.source}

    def search(self, term: str, top_n: int = 10) -> list:
        """Direct FETCH against the loaded capabilities (mirrors Corpus.search)."""
        from library_builder import solver as _solver
        from library_builder.solver import Need, fetch, _kw
        if not self._idf:
            self._idf = _solver._idf(self.caps)
        need = Need(text=term, keywords=_kw(term))
        ranked = fetch(need, self.caps, self._idf)
        return [(m.capability.cap_id, m.score) for m in ranked[:top_n]]

    def solve(self, requirement: str):
        from library_builder import solver as _solver
        return _solver.solve(requirement, self.caps)
