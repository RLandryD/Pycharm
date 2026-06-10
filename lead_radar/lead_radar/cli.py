"""lead_radar CLI.

Examples:
  python -m lead_radar.cli scan -o leads.xlsx
  python -m lead_radar.cli scan -q "SAP CPI Mexico" -q "PI/PO migración" --pages 2
  python -m lead_radar.cli scan --people --min-score 40 -o leads.xlsx
  python -m lead_radar.cli companies -c CEMEX -c "Grupo Truper" --people -o targets.xlsx
"""
import argparse
import sys

from .config import DEFAULT_QUERIES, Settings
from .exporter import export_xlsx
from .log import feature, log_event
from .people import build_search_links, fetch_people
from .scoring import (aggregate_companies, is_blocked, load_blocklist,
                      score_posting)
from .sources import adzuna, jsearch


def _collect(settings: Settings, queries: list, pages: int) -> list:
    postings = []
    for q in queries:
        postings.extend(jsearch.fetch(settings, q, pages=pages))
        postings.extend(adzuna.fetch(settings, q, pages=pages))
    # dedupe by (company, title) keeping first occurrence
    seen, unique = set(), []
    for p in postings:
        key = (p.company.lower().strip(), p.title.lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    log_event("postings_collected", raw=len(postings), unique=len(unique))
    return unique


def cmd_scan(args):
    settings = Settings()
    if not settings.available_sources():
        log_event("warning", message="No job-source API keys configured "
                  "(RAPIDAPI_KEY or ADZUNA_APP_ID/ADZUNA_APP_KEY). "
                  "Nothing to scan. See README.")
        return 1
    with feature("scan", queries=len(args.query), pages=args.pages):
        postings = _collect(settings, args.query, args.pages)
        for p in postings:
            score_posting(p)
        blocklist = load_blocklist(args.blocklist)
        leads = [l for l in aggregate_companies(postings) if l.score >= args.min_score]
        n0 = len(leads)
        leads = [l for l in leads if not is_blocked(l.company, blocklist)]
        if n0 != len(leads):
            log_event("blocklist_dropped", removed=n0 - len(leads))
        if args.exclude_channel:
            n1 = len(leads)
            leads = [l for l in leads if l.lead_type != "channel"]
            log_event("channel_excluded", removed=n1 - len(leads))
        log_event("companies_ranked", total=len(leads),
                  end_clients=sum(1 for l in leads if l.lead_type == "end_client"))

        links = build_search_links([l.company for l in leads],
                                   top_roles=args.top_roles)
        people = []
        if args.people:
            # never spend people-search credits on channel firms or blocked names
            targets = [l.company for l in leads if l.lead_type == "end_client"]
            for c in targets[:args.max_people_companies]:
                people.extend(fetch_people(settings, c, top_roles=args.top_roles))
        export_xlsx(args.output, leads, people, links)
        print(f"Done: {len(leads)} companies, {len(people)} people candidates, "
              f"{len(links)} search links -> {args.output}")
    return 0


def cmd_companies(args):
    """Skip job scan; run people discovery / link generation for known targets."""
    settings = Settings()
    with feature("companies", n=len(args.company)):
        links = build_search_links(args.company, top_roles=args.top_roles)
        people = []
        if args.people:
            for c in args.company:
                people.extend(fetch_people(settings, c, top_roles=args.top_roles))
        export_xlsx(args.output, [], people, links)
        print(f"Done: {len(people)} people candidates, {len(links)} search links "
              f"-> {args.output}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lead_radar")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scan", help="scan job boards, rank companies, find people")
    sc.add_argument("-q", "--query", action="append", default=None)
    sc.add_argument("--pages", type=int, default=1)
    sc.add_argument("--min-score", type=int, default=30)
    sc.add_argument("--people", action="store_true",
                    help="also query Google CSE for people (needs GOOGLE_CSE keys)")
    sc.add_argument("--top-roles", type=int, default=6)
    sc.add_argument("--max-people-companies", type=int, default=10,
                    help="CSE quota guard: only top-N companies get people lookups")
    sc.add_argument("--blocklist", default="blocklist.txt",
                    help="file with company names to drop entirely (one per line)")
    sc.add_argument("--exclude-channel", action="store_true",
                    help="drop consulting/staffing firms from the output instead of labeling them")
    sc.add_argument("-o", "--output", default="leads.xlsx")
    sc.set_defaults(func=cmd_scan)

    co = sub.add_parser("companies", help="people discovery for a known company list")
    co.add_argument("-c", "--company", action="append", required=True)
    co.add_argument("--people", action="store_true")
    co.add_argument("--top-roles", type=int, default=6)
    co.add_argument("-o", "--output", default="targets.xlsx")
    co.set_defaults(func=cmd_companies)

    se = sub.add_parser("setup", help="interactive wizard: get, validate, and save API keys")
    se.add_argument("--no-browser", action="store_true",
                    help="don't auto-open signup pages")
    se.set_defaults(func=lambda a: __import__(
        "lead_radar.setup_wizard", fromlist=["run_setup"]
    ).run_setup(open_browser=not a.no_browser))

    ck = sub.add_parser("check", help="validate currently configured API keys")
    ck.set_defaults(func=lambda a: __import__(
        "lead_radar.setup_wizard", fromlist=["run_check"]).run_check())

    args = ap.parse_args(argv)
    if getattr(args, "query", None) is None and args.cmd == "scan":
        args.query = list(DEFAULT_QUERIES)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
