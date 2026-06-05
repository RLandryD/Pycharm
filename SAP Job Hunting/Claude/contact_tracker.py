"""
layer2/contact_tracker.py — Contact tracking, follow-up flagging, priority surfacing.

All LinkedIn actions are manual. This module only stores, organizes, and reminds.

Run:
    python layer2/contact_tracker.py add --name "Ana Müller" --title "SAP Manager" \
        --company "Capgemini" --linkedin "https://linkedin.com/in/anamuller" \
        --country DE --tz "Europe/Berlin" --lang de --role sap_manager

    python layer2/contact_tracker.py list --due-today
    python layer2/contact_tracker.py update --id 3 --status connected
    python layer2/contact_tracker.py followup
    python layer2/contact_tracker.py import-csv --file contacts_import.csv
"""

import argparse
import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from database import get_connection, init_db

# ─────────────────────────────────────────────────────────────
ROLE_CATEGORIES = {
    "sap_manager":      "SAP Integration/CPI Manager or Director",
    "hiring_manager":   "Hiring Manager / Director / Head of HR",
    "talent_acq":       "Talent Acquisition Manager / Director / Head of TA",
    "it_director":      "IT Director / VP of Technology (SAP-adjacent)",
    "sap_practice":     "SAP Practice Lead / CoE Lead",
    "other":            "Other",
}

STATUS_CHOICES = [
    "pending", "connected", "no_response", "replied", "meeting_set", "declined"
]

STATUS_EMOJI = {
    "pending":      "⏳",
    "connected":    "🔗",
    "no_response":  "🔇",
    "replied":      "💬",
    "meeting_set":  "📅",
    "declined":     "❌",
}

PRIORITY_LABELS = {1: "🔴 HIGH", 2: "🟡 MED ", 3: "⚪ LOW "}

# Auto follow-up days per status
FOLLOWUP_DAYS = {
    "pending":      7,
    "connected":    5,
    "replied":      3,
    "no_response":  14,
}


# ─────────────────────────────────────────────────────────────
# CORE FUNCTIONS
# ─────────────────────────────────────────────────────────────

def add_contact(full_name, title=None, company_name=None, company_id=None,
                linkedin_url=None, email=None, timezone=None, country=None,
                city=None, language_preference="en", connection_status="pending",
                priority=2, role_category="other", notes=None,
                follow_up_days=None):
    """Add a new contact."""
    today = date.today().isoformat()
    followup_default = follow_up_days or FOLLOWUP_DAYS.get(connection_status, 7)
    follow_up_due = (date.today() + timedelta(days=followup_default)).isoformat()

    # Try to resolve company_id from name if not given
    if not company_id and company_name:
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM companies WHERE name LIKE ?", (f"%{company_name}%",)).fetchone()
            if row:
                company_id = row["id"]

    with get_connection() as conn:
        cur = conn.execute("""
            INSERT INTO contacts (
                full_name, title, company_id, company_name,
                linkedin_url, email, timezone, country, city,
                language_preference, connection_status,
                last_interaction, follow_up_due, priority,
                role_category, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (full_name, title, company_id, company_name,
              linkedin_url, email, timezone, country, city,
              language_preference, connection_status,
              today, follow_up_due, priority, role_category, notes))
        cid = cur.lastrowid
    print(f"[Layer 2] Contact added: {full_name} (ID #{cid}), follow-up due {follow_up_due}")
    return cid


def update_contact(contact_id, **kwargs):
    """Update any field on a contact. Also auto-sets next follow-up date on status change."""
    if not kwargs:
        print("[Layer 2] Nothing to update.")
        return

    kwargs["updated_at"] = "datetime('now')"

    # Auto-update follow_up_due when status changes
    if "connection_status" in kwargs and "follow_up_due" not in kwargs:
        days = FOLLOWUP_DAYS.get(kwargs["connection_status"])
        if days:
            kwargs["follow_up_due"] = (date.today() + timedelta(days=days)).isoformat()
        kwargs["last_interaction"] = date.today().isoformat()

    # Build SET clause (skip None values)
    set_parts = []
    values = []
    for k, v in kwargs.items():
        if v is not None:
            if v == "datetime('now')":
                set_parts.append(f"{k} = datetime('now')")
            else:
                set_parts.append(f"{k} = ?")
                values.append(v)
    values.append(contact_id)

    with get_connection() as conn:
        conn.execute(f"UPDATE contacts SET {', '.join(set_parts)} WHERE id = ?", values)
    print(f"[Layer 2] Contact #{contact_id} updated.")


def list_contacts(status=None, due_today=False, due_this_week=False,
                  priority=None, role=None, country=None, limit=100):
    """List contacts with filters."""
    query = "SELECT * FROM contacts WHERE 1=1"
    params = []

    if status:
        query += " AND connection_status = ?"
        params.append(status)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    if role:
        query += " AND role_category = ?"
        params.append(role)
    if country:
        query += " AND country = ?"
        params.append(country)

    today = date.today().isoformat()
    week_end = (date.today() + timedelta(days=7)).isoformat()

    if due_today:
        query += " AND follow_up_due <= ?"
        params.append(today)
    elif due_this_week:
        query += " AND follow_up_due <= ?"
        params.append(week_end)

    query += " ORDER BY priority ASC, follow_up_due ASC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    _print_contacts_table(rows)
    return rows


def _print_contacts_table(rows):
    today = date.today().isoformat()
    print(f"\n{'─'*120}")
    print(f"{'ID':<5} {'Pri':<8} {'Name':<25} {'Title':<35} {'Company':<22} "
          f"{'Status':<16} {'Country':<8} {'Lang':<5} {'Follow-up':<12}")
    print(f"{'─'*120}")
    for r in rows:
        overdue = r["follow_up_due"] and r["follow_up_due"] < today
        due_str = r["follow_up_due"] or "—"
        if overdue:
            due_str = f"⚠️ {due_str}"
        st = STATUS_EMOJI.get(r["connection_status"], "?") + " " + r["connection_status"]
        print(f"{r['id']:<5} {PRIORITY_LABELS.get(r['priority'], '?'):<8} "
              f"{r['full_name'][:23]:<25} {(r['title'] or '')[:33]:<35} "
              f"{(r['company_name'] or '')[:20]:<22} {st:<16} "
              f"{(r['country'] or ''):<8} {(r['language_preference'] or ''):<5} {due_str}")
    print(f"{'─'*120}")
    print(f"Total: {len(rows)} contacts\n")


def followup_dashboard():
    """Show priority contacts needing follow-up."""
    today = date.today().isoformat()
    week = (date.today() + timedelta(days=7)).isoformat()

    with get_connection() as conn:
        overdue = conn.execute(
            "SELECT * FROM contacts WHERE follow_up_due < ? AND connection_status NOT IN ('declined','meeting_set') ORDER BY priority, follow_up_due",
            (today,)
        ).fetchall()
        this_week = conn.execute(
            "SELECT * FROM contacts WHERE follow_up_due BETWEEN ? AND ? AND connection_status NOT IN ('declined','meeting_set') ORDER BY priority, follow_up_due",
            (today, week)
        ).fetchall()

    print("\n" + "═"*50)
    print("  📋  FOLLOW-UP DASHBOARD")
    print("═"*50)

    print(f"\n🚨 OVERDUE ({len(overdue)} contacts):")
    if overdue:
        _print_contacts_table(overdue)
    else:
        print("  ✅ None overdue!\n")

    print(f"\n📅 DUE THIS WEEK ({len(this_week)} contacts):")
    if this_week:
        _print_contacts_table(this_week)
    else:
        print("  ✅ None this week!\n")


def get_contact(contact_id: int):
    with get_connection() as conn:
        return conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()


def import_from_csv(filepath: str):
    """
    Bulk import contacts from a CSV file.
    Required columns: full_name
    Optional: title, company_name, linkedin_url, email, country, timezone,
              language_preference, role_category, priority, notes
    """
    path = Path(filepath)
    if not path.exists():
        print(f"[Layer 2] File not found: {filepath}")
        return

    added = 0
    skipped = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("full_name", "").strip()
            if not name:
                skipped += 1
                continue
            try:
                add_contact(
                    full_name=name,
                    title=row.get("title"),
                    company_name=row.get("company_name"),
                    linkedin_url=row.get("linkedin_url"),
                    email=row.get("email"),
                    timezone=row.get("timezone"),
                    country=row.get("country"),
                    city=row.get("city"),
                    language_preference=row.get("language_preference", "en"),
                    connection_status=row.get("connection_status", "pending"),
                    priority=int(row.get("priority", 2)),
                    role_category=row.get("role_category", "other"),
                    notes=row.get("notes"),
                )
                added += 1
            except Exception as e:
                print(f"  ⚠️  Skipped '{name}': {e}")
                skipped += 1
    print(f"[Layer 2] Import complete: {added} added, {skipped} skipped")


def export_contacts_csv(filepath="data/contacts_export.csv"):
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM contacts").fetchall()
    if not rows:
        print("[Layer 2] No contacts to export.")
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    print(f"[Layer 2] Exported {len(rows)} contacts to {path}")


def show_contact_detail(contact_id: int):
    """Show all fields for a single contact."""
    row = get_contact(contact_id)
    if not row:
        print(f"[Layer 2] Contact #{contact_id} not found.")
        return
    print(f"\n{'═'*50}")
    print(f"  Contact Detail — #{row['id']}")
    print(f"{'═'*50}")
    for key in row.keys():
        val = row[key]
        if val is not None and val != "":
            print(f"  {key:<25}: {val}")
    print()


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description="SAP Job Hunter — Layer 2: Contact Tracker")
    sub = p.add_subparsers(dest="action", required=True)

    # add
    add = sub.add_parser("add", help="Add a new contact")
    add.add_argument("--name", required=True, dest="full_name")
    add.add_argument("--title")
    add.add_argument("--company")
    add.add_argument("--linkedin")
    add.add_argument("--email")
    add.add_argument("--tz", help="Timezone, e.g. Europe/Berlin")
    add.add_argument("--country", help="2-letter country code, e.g. DE")
    add.add_argument("--city")
    add.add_argument("--lang", default="en", choices=["en", "es", "de"],
                     dest="language_preference")
    add.add_argument("--status", default="pending", choices=STATUS_CHOICES)
    add.add_argument("--priority", type=int, default=2, choices=[1, 2, 3])
    add.add_argument("--role", default="other", choices=list(ROLE_CATEGORIES.keys()))
    add.add_argument("--notes")

    # update
    upd = sub.add_parser("update", help="Update a contact")
    upd.add_argument("--id", type=int, required=True)
    upd.add_argument("--status", choices=STATUS_CHOICES)
    upd.add_argument("--priority", type=int, choices=[1, 2, 3])
    upd.add_argument("--followup", help="Next follow-up date (YYYY-MM-DD)", dest="follow_up_due")
    upd.add_argument("--email")
    upd.add_argument("--notes")
    upd.add_argument("--interaction", help="Last interaction date", dest="last_interaction")

    # list
    ls = sub.add_parser("list", help="List contacts")
    ls.add_argument("--status", choices=STATUS_CHOICES)
    ls.add_argument("--priority", type=int, choices=[1, 2, 3])
    ls.add_argument("--role", choices=list(ROLE_CATEGORIES.keys()))
    ls.add_argument("--country")
    ls.add_argument("--due-today", action="store_true")
    ls.add_argument("--due-week", action="store_true")

    # followup dashboard
    sub.add_parser("followup", help="Show follow-up dashboard")

    # detail
    det = sub.add_parser("detail", help="Show contact detail")
    det.add_argument("--id", type=int, required=True)

    # import CSV
    imp = sub.add_parser("import-csv", help="Bulk import contacts from CSV")
    imp.add_argument("--file", required=True)

    # export CSV
    exp = sub.add_parser("export", help="Export contacts to CSV")
    exp.add_argument("--out", default="data/contacts_export.csv")

    return p


def main():
    init_db()
    parser = build_parser()
    args = parser.parse_args()

    if args.action == "add":
        add_contact(
            full_name=args.full_name,
            title=args.title,
            company_name=args.company,
            linkedin_url=args.linkedin,
            email=args.email,
            timezone=args.tz,
            country=args.country,
            city=args.city,
            language_preference=args.language_preference,
            connection_status=args.status,
            priority=args.priority,
            role_category=args.role,
            notes=args.notes,
        )
    elif args.action == "update":
        kwargs = {}
        for field in ["status", "priority", "follow_up_due", "email",
                      "notes", "last_interaction"]:
            v = getattr(args, field.replace("-", "_"), None)
            if field == "status":
                v = args.status
                field = "connection_status"
            if v is not None:
                kwargs[field] = v
        update_contact(args.id, **kwargs)
    elif args.action == "list":
        list_contacts(
            status=args.status,
            due_today=args.due_today,
            due_this_week=args.due_week,
            priority=args.priority,
            role=args.role,
            country=args.country,
        )
    elif args.action == "followup":
        followup_dashboard()
    elif args.action == "detail":
        show_contact_detail(args.id)
    elif args.action == "import-csv":
        import_from_csv(args.file)
    elif args.action == "export":
        export_contacts_csv(args.out)


if __name__ == "__main__":
    main()
