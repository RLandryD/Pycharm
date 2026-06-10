# Schema dedup / clustering report

## Collapse summary

```
EDMX: 9 files → 9 exact-unique → 9 structural-unique → 9 families
```

Tiers: **exact** (byte-identical, drop dupes) → **structural** (same shape, differs only in host/whitespace) → **family** (same logical schema across releases — keep the richest superset).

**9 files → 9 canonical schemas** (one richest member per family).
