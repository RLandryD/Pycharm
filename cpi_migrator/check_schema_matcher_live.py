"""
check_schema_matcher_live.py  —  run from your project root:

    cd ~/PycharmProjects/cpi_migrator
    python3 check_schema_matcher_live.py

Validates the schema matcher against your REAL canonical library and your REAL
interface names. This is the thing offline pytest could NOT verify (it used
synthetic schemas with clean names). Nothing here touches the tenant.

STEP 1: point LIBRARY at your organized canonical library.
STEP 2: put 2-5 of your real interfaces in EXAMPLES — the message-interface
        name + namespace as they appear in PI, and (if you know it) the schema
        file you'd EXPECT it to resolve to. Leave `expect` as "" if unsure.
"""
import time
from pathlib import Path

# ---- STEP 1: your canonical library --------------------------------------
LIBRARY = "/home/landry/PycharmProjects/Resources/canonical_library"

# ---- STEP 2: your real interfaces ----------------------------------------
# (message_interface, namespace, expected_filename_substring_or_blank)
EXAMPLES = [
    # ("OrderRequest_Out", "http://yourco.com/xi/SD/Orders", "Order"),
    # ("MATMAS.MATMAS05",  "urn:sap-com:document:sap:idoc:messages", "MATMAS"),
    # ("API_BUSINESS_PARTNER", "", "API_BUSINESS_PARTNER"),
]
# --------------------------------------------------------------------------

from extractor.schema_matcher import SchemaIndex

def main():
    lib = Path(LIBRARY)
    if not lib.exists():
        print(f"!! LIBRARY not found: {LIBRARY}\n   Fix the LIBRARY path at the top.")
        return

    t0 = time.time()
    idx = SchemaIndex.build(LIBRARY)
    dt = time.time() - t0

    # index health
    by_kind, with_ns, with_names = {}, 0, 0
    for e in idx.entries:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        with_ns += 1 if e.namespace else 0
        with_names += 1 if e.names else 0
    print("=" * 70)
    print(f"INDEX: {len(idx.entries)} schemas in {dt:.1f}s   {by_kind}")
    print(f"  with a namespace: {with_ns}   with named elements: {with_names}")
    if len(idx.entries) == 0:
        print("  !! Indexed 0 schemas — is the library laid out as edmx/ wsdl/ xsd/ ?")
        return

    if not EXAMPLES:
        print("\nNo EXAMPLES yet. Add 2-5 real interfaces at the top, then re-run.")
        print("Tip: paste real PI message-interface names + namespaces so the")
        print("scoring gets tested against your actual naming.")
        return

    print("\n" + "=" * 70)
    print("MATCHES (top 3 per interface). 6+ = confident (ns+name exact).")
    confident = weak = none = 0
    for name, ns, expect in EXAMPLES:
        hits = idx.match(name=name, namespace=ns, top=3)
        print(f"\n[{name}]  ns={ns or '(none)'}")
        if not hits:
            none += 1
            print("   -> NO MATCH")
            continue
        best = hits[0].score
        confident += 1 if best >= 6 else 0
        weak += 1 if 0 < best < 6 else 0
        for h in hits:
            tag = "CONFIDENT" if h.score >= 6 else "weak"
            flag = ""
            if expect and expect.lower() in Path(h.entry.path).name.lower():
                flag = "  <-- matches your EXPECT"
            print(f"   {h.score:>2} [{tag:9}] {Path(h.entry.path).name}  {h.reasons}{flag}")

    print("\n" + "=" * 70)
    print(f"SUMMARY: {confident} confident, {weak} weak, {none} no-match "
          f"(of {len(EXAMPLES)} interfaces)")
    print("Weak/no-match interfaces are where the scoring needs tuning to your")
    print("naming. Send me a few of those lines and I'll adjust the matcher.")

if __name__ == "__main__":
    main()
