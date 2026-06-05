# Workbench Tab Optimization Audit (Gap 5)

**Report only — no changes made.** This is for your review. You know your daily
workflow; treat these as hypotheses to confirm, not decisions. Nothing in the
workbench was moved or merged.

## The 10 main tabs

| # | Tab | Purpose | Daily use? |
|---|-----|---------|-----------|
| 0 | 🔑 Profiles | Tenant/connection setup (auth, hosts, keys) | **Setup-once**, not daily |
| 1 | 📥 Source | Upload CPI packages / MA Excel / requirements | Daily (entry point) |
| 2 | 🔍 Interfaces | Parse + select interfaces to migrate | Daily |
| 3 | 🎯 Match iFlow | Find a standard iFlow (tenant → Hub → GitHub) | Daily |
| 4 | ⚙ Configure | Set adapter/auth/connectivity per interface | Daily |
| 5 | 🚀 Generate | Build iFlow + scripts + mapping bundle | Daily (core deliverable) |
| 6 | 🛡 Clean Core | Clean-core compliance check | Post-gen quality gate |
| 7 | ✅ Verify | Verify generated artifacts | Post-gen quality gate |
| 8 | 🤖 AI Solver | Capability solver + editable fields | New / occasional |
| 9 | 📋 Client Tracker | Track client engagements (3 sub-tabs) | Project-mgmt, not per-interface |

Plus nested tab-sets: APIM (6 sub-tabs), Config (multiple), Client Tracker (3),
Insights (3). That's a lot of surface.

## Findings & recommendations

### 1. Merge candidates (sequential stages of one flow)

**Clean Core (6) + Verify (7) → one "Validate" tab.** Both are post-generation
quality gates that run on the same artifacts right after Generate. They're almost
always used together, back-to-back. Merging into one "✅ Validate" tab with two
sections (clean-core + verification) would cut a tab and match the actual
workflow. **Low risk — they don't overlap in function, they're sequential.**

**Match iFlow (3) + Configure (4) — possible merge, lower confidence.** You match
an iFlow, then configure it. They're coupled, but configuration is substantial
enough that a merge might crowd the screen. **Recommend: leave separate unless
you find yourself bouncing between them constantly.**

### 2. The real overlap to examine: Match iFlow (3) vs AI Solver (8)

This is the one flagged earlier. Both answer "what existing thing fits this
need?":
- **Match iFlow (3)** searches *external/tenant sources* (your tenant, the
  Business Accelerator Hub, GitHub recipes) for a standard iFlow to start from.
- **AI Solver (8)** searches the *learned capability corpus* (built from your own
  uploaded packages) and now also drafts real artifacts + editable fields.

**Honest read:** they're *complementary*, not duplicative — Match looks
*outward* (SAP/community standard content), Solver looks *inward* (your learned
corpus). But to a client watching, two "find me a match" tabs could read as
confusing or redundant. **Recommendation: don't merge yet, but consider reframing
the labels** so the distinction is obvious — e.g. "🎯 Match (standard content)"
vs "🧠 Solve (your learned library)". The function is fine; the *naming* invites
the confusion. This is the lowest-risk way to address the overlap.

### 3. Setup-once → move out of the daily tab row

**Profiles (0)** is tenant/connection setup — you configure it once per
environment, not per interface. **Recommendation: move it to a sidebar "Settings"
or a gear menu**, freeing a top-tab slot and decluttering the daily row. (Keep it
reachable, just not occupying prime tab space.)

**Client Tracker (9)** is project-management, a different mode from the
per-interface migration pipeline. **Recommendation: consider a top-level mode
switch** (Migration | Tracker) rather than a tab inside the migration flow — but
this is preference, low priority.

### 4. Automation opportunity (not a tab change)

The linear path **Source (1) → Interfaces (2) → Match (3) → Configure (4) →
Generate (5)** is clicked through per run. For the common case, a **"Run pipeline"
button** (already partially exists in Generate's pipeline-mode) could chain
parse → match → generate with defaults, leaving the tabs for when you need to
intervene. **Recommendation: surface the existing pipeline-mode more prominently
as the "fast path," keeping the tabs as the "manual path."**

## Suggested priority (if you act on any)

1. **Reframe Match vs Solver labels** (tiny change, removes the confusing overlap)
   — safest, clearest win.
2. **Merge Clean Core + Verify into "Validate"** — one fewer tab, matches workflow.
3. **Move Profiles to settings/sidebar** — declutters the daily row.
4. Leave Match+Configure separate; revisit only if you bounce between them.
5. Surface pipeline-mode as the fast path.

## What I did NOT do

Per our agreement, I changed nothing — no tabs merged, moved, or relabeled. These
are recommendations for your decision. When you pick which to act on, I'll do them
**one at a time**, each tested, so your muscle-memory isn't disrupted by a big
rearrange. The honest constraint: you live in this UI daily and I can't render it,
so your judgment on what actually helps outranks my read of the code.
