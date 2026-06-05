"""
layer4/personalized_message.py — AI-powered hyper-personalized outreach generator.

Uses the Anthropic Claude API to generate a unique, localized first message per contact.
Adapts: language, local greeting, contact's role, company context, timezone, city.
Reads contact data from the SQLite DB or directly from a CSV export.

Run:
    python layer4/personalized_message.py generate --contact-id 3
    python layer4/personalized_message.py generate --contact-id 3 --lang de
    python layer4/personalized_message.py generate-from-csv --file contacts_export.csv --id 5
    python layer4/personalized_message.py list-drafts
    python layer4/personalized_message.py view-draft --draft-id 1
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from database import get_connection, init_db

# ─────────────────────────────────────────────────────────────
# YOUR PROFILE — used in AI context
# ─────────────────────────────────────────────────────────────

MY_PROFILE = """
Name: [Your Name]
Current Role: SAP CPI/BTP Integration Consultant
Company: Deloitte
Experience: 3 years
Clients: Fortune 500 companies
Certifications: SAP Integration Suite Certified (2023 & 2025)
Languages: Native Spanish, C1 English, A2 German
Location: México
Open to: Fully remote (worldwide) OR relocation to DACH, Canada, Spain, Nordic countries
Key skills: SAP CPI, SAP BTP, SAP Integration Suite, API Management, iFlows, OData
"""

# ─────────────────────────────────────────────────────────────
# LOCAL GREETINGS by country code
# ─────────────────────────────────────────────────────────────

LOCAL_GREETINGS = {
    "DE": {"greeting": "Guten Tag", "lang": "de"},
    "AT": {"greeting": "Servus", "lang": "de"},
    "CH": {"greeting": "Grüezi", "lang": "de"},
    "ES": {"greeting": "Hola", "lang": "es"},
    "MX": {"greeting": "Hola", "lang": "es"},
    "CO": {"greeting": "Hola", "lang": "es"},
    "AR": {"greeting": "Hola", "lang": "es"},
    "SE": {"greeting": "Hej", "lang": "en"},
    "NO": {"greeting": "Hei", "lang": "en"},
    "DK": {"greeting": "Hej", "lang": "en"},
    "FI": {"greeting": "Hei", "lang": "en"},
    "CA": {"greeting": "Hi", "lang": "en"},
    "US": {"greeting": "Hi", "lang": "en"},
    "UK": {"greeting": "Hi", "lang": "en"},
    "GB": {"greeting": "Hi", "lang": "en"},
    "NL": {"greeting": "Hallo", "lang": "en"},
    "BE": {"greeting": "Bonjour / Hallo", "lang": "en"},
    "FR": {"greeting": "Bonjour", "lang": "en"},
    "IN": {"greeting": "Hi", "lang": "en"},
    "JP": {"greeting": "Hi", "lang": "en"},
}

DEFAULT_GREETING = {"greeting": "Hi", "lang": "en"}

# ─────────────────────────────────────────────────────────────
# ROLE DESCRIPTIONS for AI prompt
# ─────────────────────────────────────────────────────────────

ROLE_CONTEXT = {
    "sap_manager":    "an SAP Manager or Director who leads SAP CPI/BTP or Integration projects",
    "hiring_manager": "a Hiring Manager, Head of HR, or HR Director responsible for recruitment",
    "talent_acq":     "a Talent Acquisition Manager, Director, or Head of TA responsible for sourcing and hiring",
    "it_director":    "an IT Director or VP of Technology with SAP-adjacent responsibilities",
    "sap_practice":   "an SAP Practice Lead or Center of Excellence (CoE) Lead",
    "other":          "a professional at this company",
}

# ─────────────────────────────────────────────────────────────
# ANTHROPIC API CALL
# ─────────────────────────────────────────────────────────────

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"


def call_claude(prompt: str, max_tokens: int = 800) -> str:
    """Call the Anthropic Claude API and return the text response."""
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    headers = {"Content-Type": "application/json"}

    response = requests.post(ANTHROPIC_API_URL, json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()

    # Extract text from content blocks
    texts = [block["text"] for block in data.get("content", []) if block.get("type") == "text"]
    return "\n".join(texts).strip()


# ─────────────────────────────────────────────────────────────
# PROMPT BUILDER
# ─────────────────────────────────────────────────────────────

def build_prompt(contact: dict, lang_override: str = None) -> str:
    country = (contact.get("country") or "").upper()
    greeting_info = LOCAL_GREETINGS.get(country, DEFAULT_GREETING)
    local_greeting = greeting_info["greeting"]

    # Determine language
    lang = lang_override or contact.get("language_preference") or greeting_info["lang"] or "en"

    lang_instruction = {
        "en": "Write the message entirely in English.",
        "es": "Escribe el mensaje completamente en español.",
        "de": "Schreibe die Nachricht vollständig auf Deutsch.",
    }.get(lang, "Write the message in English.")

    role_desc = ROLE_CONTEXT.get(contact.get("role_category") or "other", ROLE_CONTEXT["other"])
    company = contact.get("company_name") or "their company"
    city = contact.get("city") or ""
    timezone = contact.get("timezone") or ""
    first_name = contact["full_name"].split()[0]

    location_hint = ""
    if city:
        location_hint = f"They are based in {city}"
        if country:
            location_hint += f", {country}"
        location_hint += "."

    timezone_hint = f"Their timezone is {timezone}." if timezone else ""

    prompt = f"""You are helping a professional write a highly personalized LinkedIn outreach message or email.

## SENDER PROFILE:
{MY_PROFILE}

## RECIPIENT:
- Full name: {contact['full_name']}
- First name: {first_name}
- Title: {contact.get('title') or 'Unknown'}
- Role type: {role_desc}
- Company: {company}
- {location_hint}
- {timezone_hint}
- Preferred language: {lang.upper()}
- Local greeting in their region: "{local_greeting}"

## TASK:
Write a short, warm, professional outreach message that:
1. Opens with the culturally appropriate local greeting: "{local_greeting} {first_name},"
2. Is natural and human — NOT generic, NOT AI-sounding
3. Acknowledges their specific role (not just their name)
4. Briefly introduces the sender's SAP CPI/BTP background in a relevant way
5. Has a clear, low-friction call to action (15-minute chat, or asking if there's interest)
6. Feels like it was written by a real person who did their homework
7. Is concise — maximum 180 words
8. Ends with a professional sign-off using the sender's name

## LANGUAGE REQUIREMENT:
{lang_instruction}

## FORMAT:
Return ONLY the message body. No subject line. No extra explanation. No markdown. Just the message text.
"""
    return prompt, lang


# ─────────────────────────────────────────────────────────────
# GENERATE & SAVE
# ─────────────────────────────────────────────────────────────

def generate_message(contact_id: int, lang_override: str = None, save: bool = True) -> dict:
    """Generate a personalized message for a contact by DB ID."""
    with get_connection() as conn:
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        print(f"[Layer 4] Contact #{contact_id} not found.")
        return {}

    contact = dict(contact)
    return _run_generation(contact, lang_override, save)


def generate_message_from_csv_row(csv_row: dict, lang_override: str = None) -> dict:
    """Generate a personalized message from a raw CSV row dict (no DB required)."""
    return _run_generation(csv_row, lang_override, save=False)


def _run_generation(contact: dict, lang_override: str = None, save: bool = True) -> dict:
    """Core generation logic."""
    print(f"[Layer 4] Generating message for: {contact['full_name']}...")
    prompt, lang = build_prompt(contact, lang_override)

    try:
        body = call_claude(prompt)
    except requests.HTTPError as e:
        print(f"[Layer 4] API error: {e}")
        if e.response is not None:
            print(f"         Response: {e.response.text}")
        return {}
    except Exception as e:
        print(f"[Layer 4] Unexpected error: {e}")
        return {}

    result = {
        "contact_id": contact.get("id"),
        "contact_name": contact["full_name"],
        "company": contact.get("company_name"),
        "lang": lang,
        "body": body,
    }

    if save and contact.get("id"):
        with get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO message_drafts (contact_id, language, body)
                VALUES (?, ?, ?)
            """, (contact["id"], lang, body))
            result["draft_id"] = cur.lastrowid
        print(f"[Layer 4] Draft saved (ID #{result['draft_id']})")

    return result


def print_message_result(result: dict):
    if not result:
        return
    print(f"\n{'═'*60}")
    print(f"  PERSONALIZED MESSAGE — {result.get('contact_name')}")
    print(f"  Company: {result.get('company') or '—'}  |  Language: {result.get('lang', '').upper()}")
    if result.get("draft_id"):
        print(f"  Draft ID: #{result['draft_id']}")
    print(f"{'─'*60}")
    print(result.get("body", ""))
    print(f"{'═'*60}\n")


# ─────────────────────────────────────────────────────────────
# DRAFT MANAGEMENT
# ─────────────────────────────────────────────────────────────

def list_drafts(contact_id: int = None):
    """List all saved message drafts."""
    query = """
        SELECT d.id, d.generated_at, d.language, d.used,
               c.full_name, c.company_name
        FROM message_drafts d
        LEFT JOIN contacts c ON c.id = d.contact_id
    """
    params = []
    if contact_id:
        query += " WHERE d.contact_id = ?"
        params.append(contact_id)
    query += " ORDER BY d.generated_at DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    print(f"\n{'─'*90}")
    print(f"{'DraftID':<9} {'Generated':<22} {'Lang':<6} {'Used':<6} {'Contact':<25} {'Company'}")
    print(f"{'─'*90}")
    for r in rows:
        used = "✓" if r["used"] else "—"
        print(f"{r['id']:<9} {r['generated_at']:<22} {r['language']:<6} {used:<6} "
              f"{(r['full_name'] or '')[:23]:<25} {r['company_name'] or ''}")
    print(f"{'─'*90}")
    print(f"Total: {len(rows)} draft(s)\n")


def view_draft(draft_id: int):
    """Print full draft content."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT d.*, c.full_name, c.company_name
            FROM message_drafts d
            LEFT JOIN contacts c ON c.id = d.contact_id
            WHERE d.id = ?
        """, (draft_id,)).fetchone()
    if not row:
        print(f"[Layer 4] Draft #{draft_id} not found.")
        return
    print(f"\n{'═'*60}")
    print(f"  Draft #{row['id']} — {row['full_name']} at {row['company_name']}")
    print(f"  Language: {row['language'].upper()}  |  Generated: {row['generated_at']}")
    print(f"{'─'*60}")
    print(row["body"])
    print(f"{'═'*60}\n")


def mark_draft_used(draft_id: int):
    with get_connection() as conn:
        conn.execute("UPDATE message_drafts SET used=1 WHERE id=?", (draft_id,))
    print(f"[Layer 4] Draft #{draft_id} marked as used.")


# ─────────────────────────────────────────────────────────────
# CSV-BASED GENERATION (no DB needed)
# ─────────────────────────────────────────────────────────────

def generate_from_csv(filepath: str, row_id: int = None, lang_override: str = None):
    """
    Generate personalized messages from a CSV contacts export.
    If row_id is given, generate only for that row (1-indexed).
    Otherwise generate for all rows.
    """
    path = Path(filepath)
    if not path.exists():
        print(f"[Layer 4] File not found: {filepath}")
        return

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("[Layer 4] CSV is empty.")
        return

    if row_id is not None:
        # Support both 0- and 1-indexed
        idx = row_id - 1
        if idx < 0 or idx >= len(rows):
            print(f"[Layer 4] Row {row_id} not found (file has {len(rows)} rows).")
            return
        targets = [rows[idx]]
    else:
        targets = rows

    for i, row in enumerate(targets, 1):
        if not row.get("full_name"):
            print(f"[Layer 4] Row {i}: no full_name, skipping.")
            continue
        result = generate_message_from_csv_row(row, lang_override)
        print_message_result(result)
        if len(targets) > 1 and i < len(targets):
            time.sleep(1)  # gentle rate limiting


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        description="SAP Job Hunter — Layer 4: AI-Powered Personalized Message Generator"
    )
    sub = p.add_subparsers(dest="action", required=True)

    gen = sub.add_parser("generate", help="Generate message for a contact by DB ID")
    gen.add_argument("--contact-id", type=int, required=True)
    gen.add_argument("--lang", choices=["en", "es", "de"])
    gen.add_argument("--no-save", action="store_true")

    csv_gen = sub.add_parser("generate-from-csv", help="Generate from CSV contacts export")
    csv_gen.add_argument("--file", required=True)
    csv_gen.add_argument("--id", type=int, dest="row_id",
                         help="Row number (1-indexed) to generate for. Omit for all.")
    csv_gen.add_argument("--lang", choices=["en", "es", "de"])

    sub.add_parser("list-drafts", help="List all saved message drafts")

    vd = sub.add_parser("view-draft", help="View a specific draft")
    vd.add_argument("--draft-id", type=int, required=True)

    mu = sub.add_parser("mark-used", help="Mark a draft as used/sent")
    mu.add_argument("--draft-id", type=int, required=True)

    return p


def main():
    init_db()
    parser = build_parser()
    args = parser.parse_args()

    if args.action == "generate":
        result = generate_message(
            contact_id=args.contact_id,
            lang_override=args.lang,
            save=not args.no_save,
        )
        print_message_result(result)

    elif args.action == "generate-from-csv":
        generate_from_csv(args.file, args.row_id, args.lang)

    elif args.action == "list-drafts":
        list_drafts()

    elif args.action == "view-draft":
        view_draft(args.draft_id)

    elif args.action == "mark-used":
        mark_draft_used(args.draft_id)


if __name__ == "__main__":
    main()
