#!/usr/bin/env python3
"""
build_store.py  --  CLI to distill a raw corpus into a persistent CorpusStore,
incrementally update it, or inspect it. This is the OFFLINE entry point that
keeps the expensive walk out of the UI request path.

Usage:
  # distill the full raw corpus ONCE into an editable, sharded store
  python3 -m library_builder.build_store <raw_corpus_path> --out Resources/Corpus

  # later: add something new you found (add-only, deduped) -- no full re-walk
  python3 -m library_builder.build_store <new_file_or_folder> --out Resources/Corpus --update

  # inspect what's stored
  python3 -m library_builder.build_store --out Resources/Corpus --info

The store is read back instantly by CorpusStore.load(out); the raw corpus is
never needed again unless you choose to re-distill.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from library_builder.corpus_pipeline import build_corpus
from library_builder.corpus_store import CorpusStore


def _print(title, obj):
    print(title)
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def cmd_info(out: str) -> int:
    store = CorpusStore.load(out)
    _print(f"Corpus store at {out}:", store.report())
    return 0


def cmd_update(src: str, out: str) -> int:
    if not Path(src).exists():
        print(f"error: source not found: {src}", file=sys.stderr)
        return 2
    store = CorpusStore.load(out)        # existing library (fast)
    before = len(store.caps)
    corpus = build_corpus(path=src)      # distill ONLY the new input
    result = store.update_from_corpus(corpus)
    store.save(out)                      # rewrites only the changed shards
    _print(f"Updated store at {out} (was {before} caps):", result)
    return 0


def cmd_build(src: str, out: str) -> int:
    if not Path(src).exists():
        print(f"error: corpus path not found: {src}", file=sys.stderr)
        return 2
    print(f"Distilling corpus at {src} ... (one-time; this is the heavy step)")
    corpus = build_corpus(path=src)
    store = CorpusStore.from_corpus(corpus, source=str(src))
    index = store.save(out)
    _print(f"Saved store to {out}:", index)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="library_builder.build_store",
        description="Distill / update / inspect the persistent capability store.")
    ap.add_argument("src", nargs="?", default=None,
                    help="raw corpus path (build) or new file/folder (--update)")
    ap.add_argument("--out", required=True,
                    help="store directory, e.g. Resources/Corpus")
    ap.add_argument("--update", action="store_true",
                    help="add-only merge of `src` into the existing store")
    ap.add_argument("--info", action="store_true",
                    help="print the stored manifest and exit")
    args = ap.parse_args(argv)

    if args.info:
        return cmd_info(args.out)
    if args.src is None:
        ap.error("a source path is required unless --info is given")
    if args.update:
        return cmd_update(args.src, args.out)
    return cmd_build(args.src, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
