"""
layer1/company_db.py — Company database: scraping, seeding, and querying.

Sources used (all public, no auth required):
  - SAP partner directory (public JSON endpoint)
  - LinkedIn job search RSS / public pages (manual seed helpers)
  - A curated seed list of known SAP CPI/BTP employers

Run directly:
    python layer1/company_db.py --action seed
    python layer1/company_db.py --action list
    python layer1/company_db.py --action add --name "Accenture" --hq "Ireland"
    python layer1/company_db.py --action export --format csv
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, date
from pathlib import Path

import requests

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))
from database import get_connection, init_db

# ─────────────────────────────────────────────────────────────
# CURATED SEED DATA
# Companies known to hire SAP CPI/BTP consultants remotely or
# sponsor visas in target countries. Manually researched baseline.
# ─────────────────────────────────────────────────────────────

SEED_COMPANIES = [
    # ── Global SIs & Consultancies ──────────────────────────
    {
        "name": "Accenture",
        "website": "https://accenture.com",
        "size": "500000+",
        "hq_country": "Ireland",
        "hq_city": "Dublin",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.9,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA, Ariba",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA", "ES", "SE"]),
        "preferred_languages": "en,de,es",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Large SAP practice. Active in DACH, Canada, Spain.",
    },
    {
        "name": "Capgemini",
        "website": "https://capgemini.com",
        "size": "300000+",
        "hq_country": "France",
        "hq_city": "Paris",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.8,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, SuccessFactors",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA", "ES", "SE", "NO"]),
        "preferred_languages": "en,de,es,fr",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Strong SAP CPI practice. Offices in DACH and Nordics.",
    },
    {
        "name": "Deloitte",
        "website": "https://deloitte.com",
        "size": "400000+",
        "hq_country": "UK",
        "hq_city": "London",
        "remote_policy": "hybrid",
        "glassdoor_rating": 4.0,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 0,
        "visa_sponsorship": json.dumps(["DE", "CA", "ES"]),
        "preferred_languages": "en,de",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Your current employer. Included for benchmarking.",
    },
    {
        "name": "IBM",
        "website": "https://ibm.com",
        "size": "250000+",
        "hq_country": "USA",
        "hq_city": "Armonk, NY",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.9,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA"]),
        "preferred_languages": "en,de,es",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Strong global SAP practice. Remote-friendly roles.",
    },
    {
        "name": "Wipro",
        "website": "https://wipro.com",
        "size": "200000+",
        "hq_country": "India",
        "hq_city": "Bangalore",
        "remote_policy": "remote",
        "glassdoor_rating": 3.6,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, PI/PO",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "FI", "SE"]),
        "preferred_languages": "en",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Hires SAP Integration consultants remotely.",
    },
    {
        "name": "Infosys",
        "website": "https://infosys.com",
        "size": "300000+",
        "hq_country": "India",
        "hq_city": "Bangalore",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.7,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA", "SE"]),
        "preferred_languages": "en,de",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Active SAP practice globally.",
    },
    {
        "name": "TCS (Tata Consultancy Services)",
        "website": "https://tcs.com",
        "size": "600000+",
        "hq_country": "India",
        "hq_city": "Mumbai",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.7,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA", "SE", "NO"]),
        "preferred_languages": "en,de",
        "avoidance_flag": 0,
        "source": "seed",
    },
    {
        "name": "NTT Data",
        "website": "https://nttdata.com",
        "size": "140000+",
        "hq_country": "Japan",
        "hq_city": "Tokyo",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.7,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "ES", "SE"]),
        "preferred_languages": "en,de,es",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Strong presence in Spain and Germany.",
    },
    {
        "name": "Cognizant",
        "website": "https://cognizant.com",
        "size": "350000+",
        "hq_country": "USA",
        "hq_city": "Teaneck, NJ",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.6,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA"]),
        "preferred_languages": "en",
        "avoidance_flag": 0,
        "source": "seed",
    },
    # ── SAP-Focused Boutiques ────────────────────────────────
    {
        "name": "Syntax",
        "website": "https://syntax.com",
        "size": "1000-5000",
        "hq_country": "Canada",
        "hq_city": "Montreal",
        "remote_policy": "remote",
        "glassdoor_rating": 4.1,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA, Rise with SAP",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["CA", "DE"]),
        "preferred_languages": "en,de",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "SAP-only MSP. Fully remote positions. Great culture.",
    },
    {
        "name": "Basis Technologies",
        "website": "https://basistechnologies.com",
        "size": "100-500",
        "hq_country": "UK",
        "hq_city": "London",
        "remote_policy": "remote",
        "glassdoor_rating": 4.3,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP BTP, CPI, DevOps",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "ES"]),
        "preferred_languages": "en",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Small but excellent SAP DevOps/BTP specialist.",
    },
    {
        "name": "Birlasoft",
        "website": "https://birlasoft.com",
        "size": "10000+",
        "hq_country": "India",
        "hq_city": "Noida",
        "remote_policy": "remote",
        "glassdoor_rating": 3.8,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE"]),
        "preferred_languages": "en",
        "avoidance_flag": 0,
        "source": "seed",
    },
    {
        "name": "Atos",
        "website": "https://atos.net",
        "size": "100000+",
        "hq_country": "France",
        "hq_city": "Bezons",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.3,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "ES"]),
        "preferred_languages": "en,de,es,fr",
        "avoidance_flag": 1,
        "avoidance_reason": "Major layoffs 2023–2024, financial distress",
        "source": "seed",
    },
    # ── Nordic / DACH Specialists ────────────────────────────
    {
        "name": "Rimini Street",
        "website": "https://riministreet.com",
        "size": "1000-5000",
        "hq_country": "USA",
        "hq_city": "Las Vegas",
        "remote_policy": "remote",
        "glassdoor_rating": 3.8,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP Support, CPI",
        "is_sap_partner": 0,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps([]),
        "preferred_languages": "en,de,es",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Remote-first. Strong in SAP support services.",
    },
    {
        "name": "msg group",
        "website": "https://msg.group",
        "size": "10000+",
        "hq_country": "Germany",
        "hq_city": "Ismaning",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.9,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "AT", "CH"]),
        "preferred_languages": "de,en",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "DACH SAP specialist. Visa sponsorship for Germany.",
    },
    {
        "name": "cbs Corporate Business Solutions",
        "website": "https://cbs-consulting.com",
        "size": "1000-5000",
        "hq_country": "Germany",
        "hq_city": "Heidelberg",
        "remote_policy": "hybrid",
        "glassdoor_rating": 4.1,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 0,
        "visa_sponsorship": json.dumps(["DE", "AT", "CH"]),
        "preferred_languages": "de,en",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Pure SAP shop. Great learning culture in Germany.",
    },
    {
        "name": "Nagarro",
        "website": "https://nagarro.com",
        "size": "18000+",
        "hq_country": "Germany",
        "hq_city": "Munich",
        "remote_policy": "remote",
        "glassdoor_rating": 4.1,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "AT"]),
        "preferred_languages": "en,de",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Remote-first engineering culture. Strong in DACH.",
    },
    {
        "name": "Result Group",
        "website": "https://result-group.com",
        "size": "500-1000",
        "hq_country": "Sweden",
        "hq_city": "Stockholm",
        "remote_policy": "hybrid",
        "glassdoor_rating": 4.0,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, S/4HANA, BTP",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["SE", "DK", "NO", "FI"]),
        "preferred_languages": "en,sv",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Nordic SAP specialist. Visa sponsorship for Nordics.",
    },
    # ── End Users (Fortune 500 SAP shops) ───────────────────
    {
        "name": "Siemens",
        "website": "https://siemens.com",
        "size": "300000+",
        "hq_country": "Germany",
        "hq_city": "Munich",
        "remote_policy": "hybrid",
        "glassdoor_rating": 4.1,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP S/4HANA, BTP, CPI",
        "is_sap_partner": 0,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "AT", "CH"]),
        "preferred_languages": "de,en",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Major internal SAP team. CPI used for integrations.",
    },
    {
        "name": "SAP SE",
        "website": "https://sap.com",
        "size": "100000+",
        "hq_country": "Germany",
        "hq_city": "Walldorf",
        "remote_policy": "hybrid",
        "glassdoor_rating": 4.4,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP BTP, CPI, Integration Suite",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA", "ES", "SE"]),
        "preferred_languages": "de,en",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "The vendor itself. Strong learning culture. Excellent pay.",
    },
    {
        "name": "Bosch",
        "website": "https://bosch.com",
        "size": "400000+",
        "hq_country": "Germany",
        "hq_city": "Stuttgart",
        "remote_policy": "hybrid",
        "glassdoor_rating": 4.0,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP S/4HANA, CPI, BTP",
        "is_sap_partner": 0,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE"]),
        "preferred_languages": "de,en",
        "avoidance_flag": 0,
        "source": "seed",
    },
    {
        "name": "KPMG",
        "website": "https://kpmg.com",
        "size": "200000+",
        "hq_country": "Netherlands",
        "hq_city": "Amsterdam",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.9,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP BTP, CPI, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 0,
        "visa_sponsorship": json.dumps(["DE", "CA", "ES"]),
        "preferred_languages": "en,de",
        "avoidance_flag": 0,
        "source": "seed",
    },
    {
        "name": "EY (Ernst & Young)",
        "website": "https://ey.com",
        "size": "300000+",
        "hq_country": "UK",
        "hq_city": "London",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.9,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP, S/4HANA",
        "is_sap_partner": 1,
        "hires_contractors": 0,
        "visa_sponsorship": json.dumps(["DE", "CA", "ES", "SE"]),
        "preferred_languages": "en,de,es",
        "avoidance_flag": 0,
        "source": "seed",
    },
    {
        "name": "PwC",
        "website": "https://pwc.com",
        "size": "280000+",
        "hq_country": "UK",
        "hq_city": "London",
        "remote_policy": "hybrid",
        "glassdoor_rating": 3.9,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP",
        "is_sap_partner": 1,
        "hires_contractors": 0,
        "visa_sponsorship": json.dumps(["DE", "CA", "ES"]),
        "preferred_languages": "en,de,es",
        "avoidance_flag": 0,
        "source": "seed",
    },
    {
        "name": "Hexaware Technologies",
        "website": "https://hexaware.com",
        "size": "20000+",
        "hq_country": "India",
        "hq_city": "Mumbai",
        "remote_policy": "remote",
        "glassdoor_rating": 3.8,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP CPI, BTP",
        "is_sap_partner": 1,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["DE", "CA"]),
        "preferred_languages": "en",
        "avoidance_flag": 0,
        "source": "seed",
    },
    {
        "name": "Softchoice",
        "website": "https://softchoice.com",
        "size": "1000-5000",
        "hq_country": "Canada",
        "hq_city": "Toronto",
        "remote_policy": "remote",
        "glassdoor_rating": 4.2,
        "sap_usage_confirmed": 1,
        "sap_products": "SAP BTP, CPI",
        "is_sap_partner": 0,
        "hires_contractors": 1,
        "visa_sponsorship": json.dumps(["CA"]),
        "preferred_languages": "en",
        "avoidance_flag": 0,
        "source": "seed",
        "notes": "Canada-based. Remote roles available.",
    },
]


# ─────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────

def seed_companies():
    """Insert all seed companies, skip duplicates."""
    inserted = 0
    skipped = 0
    with get_connection() as conn:
        for c in SEED_COMPANIES:
            try:
                conn.execute("""
                    INSERT INTO companies (
                        name, website, size, hq_country, hq_city,
                        remote_policy, glassdoor_rating, sap_usage_confirmed,
                        sap_products, is_sap_partner, hires_contractors,
                        visa_sponsorship, preferred_languages,
                        avoidance_flag, avoidance_reason, source, notes
                    ) VALUES (
                        :name, :website, :size, :hq_country, :hq_city,
                        :remote_policy, :glassdoor_rating, :sap_usage_confirmed,
                        :sap_products, :is_sap_partner, :hires_contractors,
                        :visa_sponsorship, :preferred_languages,
                        :avoidance_flag, :avoidance_reason, :source, :notes
                    )
                """, {
                    "avoidance_reason": None,
                    "notes": None,
                    "website": None,
                    **c,
                })
                inserted += 1
            except Exception:
                skipped += 1
    print(f"[Layer 1] Seed complete: {inserted} inserted, {skipped} skipped (already exist)")


def add_company(name, website=None, hq_country=None, hq_city=None,
                size=None, remote_policy="unknown", glassdoor_rating=None,
                sap_products=None, is_sap_partner=0, hires_contractors=0,
                visa_sponsorship=None, preferred_languages="en",
                avoidance_flag=0, avoidance_reason=None, source="manual", notes=None):
    """Add a single company to the database."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO companies (
                name, website, hq_country, hq_city, size, remote_policy,
                glassdoor_rating, sap_usage_confirmed, sap_products,
                is_sap_partner, hires_contractors, visa_sponsorship,
                preferred_languages, avoidance_flag, avoidance_reason,
                source, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (name, website, hq_country, hq_city, size, remote_policy,
              glassdoor_rating, 1 if sap_products else 0, sap_products,
              is_sap_partner, hires_contractors,
              json.dumps(visa_sponsorship) if isinstance(visa_sponsorship, list) else visa_sponsorship,
              preferred_languages, avoidance_flag, avoidance_reason, source, notes))
    print(f"[Layer 1] Added: {name}")


def list_companies(filter_remote=False, filter_no_avoidance=True,
                   filter_visa=None, filter_language=None, limit=50):
    """List companies with optional filters."""
    query = "SELECT * FROM companies WHERE 1=1"
    params = []

    if filter_no_avoidance:
        query += " AND avoidance_flag = 0"
    if filter_remote:
        query += " AND remote_policy IN ('remote', 'hybrid')"
    if filter_language:
        query += f" AND preferred_languages LIKE ?"
        params.append(f"%{filter_language}%")
    if filter_visa:
        query += f" AND visa_sponsorship LIKE ?"
        params.append(f"%{filter_visa}%")

    query += f" ORDER BY glassdoor_rating DESC NULLS LAST LIMIT {limit}"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    print(f"\n{'─'*100}")
    print(f"{'ID':<5} {'Company':<35} {'HQ':<15} {'Size':<12} {'Remote':<10} {'Rating':<8} {'SAP Partner':<12} {'Visa'}")
    print(f"{'─'*100}")
    for r in rows:
        visa = ", ".join(json.loads(r["visa_sponsorship"])) if r["visa_sponsorship"] else "—"
        rating = f"{r['glassdoor_rating']:.1f}" if r["glassdoor_rating"] else "N/A"
        print(f"{r['id']:<5} {r['name'][:33]:<35} {(r['hq_country'] or ''):<15} "
              f"{(r['size'] or '')[:10]:<12} {r['remote_policy']:<10} {rating:<8} "
              f"{'✓' if r['is_sap_partner'] else '✗':<12} {visa[:40]}")
    print(f"{'─'*100}")
    print(f"Total: {len(rows)} companies\n")
    return rows


def update_job_post_date(company_id: int, date_str: str):
    """Update the last known job posting date for a company."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE companies SET last_job_post_date=?, updated_at=datetime('now') WHERE id=?",
            (date_str, company_id)
        )
    print(f"[Layer 1] Updated job post date for company #{company_id}")


def export_to_csv(filepath: str = "data/companies_export.csv"):
    """Export companies table to CSV."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM companies").fetchall()
    if not rows:
        print("[Layer 1] No companies to export.")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    print(f"[Layer 1] Exported {len(rows)} companies to {path}")


def update_rating(company_id: int, glassdoor: float = None, kununu: float = None):
    """Update Glassdoor or Kununu rating."""
    with get_connection() as conn:
        if glassdoor:
            conn.execute("UPDATE companies SET glassdoor_rating=? WHERE id=?", (glassdoor, company_id))
        if kununu:
            conn.execute("UPDATE companies SET kununu_rating=? WHERE id=?", (kununu, company_id))
    print(f"[Layer 1] Ratings updated for company #{company_id}")


def mark_avoidance(company_id: int, reason: str):
    """Flag a company to avoid."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE companies SET avoidance_flag=1, avoidance_reason=? WHERE id=?",
            (reason, company_id)
        )
    print(f"[Layer 1] Company #{company_id} flagged for avoidance: {reason}")


def show_flagged():
    """Show companies flagged for avoidance."""
    with get_connection() as conn:
        rows = conn.execute("SELECT id, name, avoidance_reason FROM companies WHERE avoidance_flag=1").fetchall()
    print(f"\n⚠️  Flagged companies ({len(rows)}):")
    for r in rows:
        print(f"  [{r['id']}] {r['name']}: {r['avoidance_reason']}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description="SAP Job Hunter — Layer 1: Company Database")
    sub = p.add_subparsers(dest="action", required=True)

    sub.add_parser("seed", help="Load curated seed company list")

    ls = sub.add_parser("list", help="List companies")
    ls.add_argument("--remote", action="store_true", help="Filter remote/hybrid only")
    ls.add_argument("--visa", help="Filter by visa country code (e.g. DE)")
    ls.add_argument("--lang", help="Filter by language (e.g. de)")
    ls.add_argument("--all", dest="show_all", action="store_true", help="Include flagged companies")

    add = sub.add_parser("add", help="Add a company manually")
    add.add_argument("--name", required=True)
    add.add_argument("--website")
    add.add_argument("--hq", help="HQ country")
    add.add_argument("--city")
    add.add_argument("--size")
    add.add_argument("--remote", default="unknown")
    add.add_argument("--rating", type=float)
    add.add_argument("--sap-products")
    add.add_argument("--partner", action="store_true")
    add.add_argument("--contractors", action="store_true")
    add.add_argument("--visa", help="Comma-separated country codes, e.g. DE,CA")
    add.add_argument("--langs", default="en")
    add.add_argument("--notes")

    flag = sub.add_parser("flag", help="Flag a company for avoidance")
    flag.add_argument("--id", type=int, required=True)
    flag.add_argument("--reason", required=True)

    sub.add_parser("flagged", help="Show all flagged companies")

    exp = sub.add_parser("export", help="Export to CSV")
    exp.add_argument("--out", default="data/companies_export.csv")

    jd = sub.add_parser("jobdate", help="Update last job post date")
    jd.add_argument("--id", type=int, required=True)
    jd.add_argument("--date", default=str(date.today()))

    return p


def main():
    init_db()
    parser = build_parser()
    args = parser.parse_args()

    if args.action == "seed":
        seed_companies()
    elif args.action == "list":
        list_companies(
            filter_remote=args.remote,
            filter_no_avoidance=not args.show_all,
            filter_visa=args.visa,
            filter_language=args.lang,
        )
    elif args.action == "add":
        visa_list = [v.strip() for v in args.visa.split(",")] if args.visa else []
        add_company(
            name=args.name, website=args.website,
            hq_country=args.hq, hq_city=args.city,
            size=args.size, remote_policy=args.remote,
            glassdoor_rating=args.rating, sap_products=args.sap_products,
            is_sap_partner=1 if args.partner else 0,
            hires_contractors=1 if args.contractors else 0,
            visa_sponsorship=visa_list, preferred_languages=args.langs,
            source="manual", notes=args.notes,
        )
    elif args.action == "flag":
        mark_avoidance(args.id, args.reason)
    elif args.action == "flagged":
        show_flagged()
    elif args.action == "export":
        export_to_csv(args.out)
    elif args.action == "jobdate":
        update_job_post_date(args.id, args.date)


if __name__ == "__main__":
    main()
