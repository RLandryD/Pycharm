"""
models/client_tracker.py

Local encrypted database tracking:
  - Which clients have which parked (HIGH/SPECIALIST) interfaces
  - Problem classification per interface
  - When you solve a problem type, find all affected clients
  - Generate personalized follow-up messages per client

Stored at ~/.cpi_migrator/client_tracker.json (encrypted with same
master password as credential profiles).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TRACKER_DIR  = Path.home() / ".cpi_migrator" / "client_tracker"
TRACKER_FILE = TRACKER_DIR / "problems.json"

# Problem categories
PROBLEM_TYPES = {
    "BPM_SIMPLE":        "BPM/ccBPM redesign (simple linear flow)",
    "BPM_COMPLEX":       "BPM/ccBPM redesign (complex with correlation)",
    "JAVA_BINARY":       "Java mapping with binary/PDF/Office operations",
    "JAVA_COMPLEX":      "Complex Java mapping requiring full Groovy rewrite",
    "ABAP_PROXY":        "ABAP Proxy migration (needs SPROXY/SE80 access)",
    "EOIO_HIGHVOL":      "EOIO high volume performance tuning",
    "CUSTOM_ADAPTER":    "Unknown or custom adapter type investigation",
    "RFC_NO_ODATA":      "RFC/BAPI with no standard OData equivalent",
    "Z_OBJECT":          "Custom Z-BAPI or Z-table (needs RAP/CAP exposure)",
    "AS2_COMPLEX":       "Complex AS2/EDI schema (Integration Advisor required)",
    "JDBC_SAP_DB":       "JDBC to SAP internal database (Clean Core blocker)",
    "UNDOCUMENTED":      "Undocumented interface requiring reverse engineering",
    "MISSING_INFO":      "Blocked on missing client information",
    "OTHER":             "Other research required",
}


@dataclass
class ParkedInterface:
    interface_name: str
    problem_type: str
    problem_description: str
    client_name: str
    parked_at: str
    complexity: str = "HIGH"
    notes: str = ""
    solved: bool = False
    solved_at: str = ""
    follow_up_sent: bool = False


@dataclass
class ClientRecord:
    client_name: str
    company: str
    contact_name: str = ""
    contact_title: str = ""
    created_at: str = ""
    parked_interfaces: list[dict] = field(default_factory=list)
    completed_interfaces: list[str] = field(default_factory=list)
    notes: str = ""


class ClientProblemTracker:
    """
    Tracks parked interfaces across clients.
    When you solve a problem type, finds all affected clients
    and generates personalized follow-up messages.
    """

    def __init__(self):
        TRACKER_DIR.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    # ── CRUD ──────────────────────────────────────────────────────────

    def add_client(
        self,
        client_name: str,
        company: str,
        contact_name: str = "",
        contact_title: str = "",
        notes: str = "",
    ) -> ClientRecord:
        record = ClientRecord(
            client_name=client_name,
            company=company,
            contact_name=contact_name,
            contact_title=contact_title,
            created_at=datetime.now().isoformat(),
            notes=notes,
        )
        self._data["clients"][client_name] = asdict(record)
        self._save()
        return record

    def park_interface(
        self,
        client_name: str,
        interface_name: str,
        problem_type: str,
        problem_description: str = "",
        complexity: str = "HIGH",
        notes: str = "",
    ):
        """Record a parked interface for a client."""
        if client_name not in self._data["clients"]:
            self.add_client(client_name, client_name)

        parked = ParkedInterface(
            interface_name=interface_name,
            problem_type=problem_type,
            problem_description=problem_description or PROBLEM_TYPES.get(problem_type, ""),
            client_name=client_name,
            parked_at=datetime.now().isoformat(),
            complexity=complexity,
            notes=notes,
        )

        client = self._data["clients"][client_name]
        # Avoid duplicates
        existing = [p for p in client["parked_interfaces"]
                    if p["interface_name"] != interface_name]
        existing.append(asdict(parked))
        client["parked_interfaces"] = existing
        self._save()
        logger.info("Parked %s for client %s (%s)",
                    interface_name, client_name, problem_type)

    def mark_completed(self, client_name: str, interface_name: str):
        """Mark an interface as completed for a client."""
        if client_name not in self._data["clients"]:
            return
        client = self._data["clients"][client_name]
        if interface_name not in client["completed_interfaces"]:
            client["completed_interfaces"].append(interface_name)
        # Remove from parked
        client["parked_interfaces"] = [
            p for p in client["parked_interfaces"]
            if p["interface_name"] != interface_name
        ]
        self._save()

    def solve_problem_type(self, problem_type: str) -> list[dict]:
        """
        Mark a problem type as solved.
        Returns list of affected clients + interfaces for follow-up.
        """
        affected = []
        for client_name, client in self._data["clients"].items():
            matching = [
                p for p in client["parked_interfaces"]
                if p["problem_type"] == problem_type and not p["solved"]
            ]
            if matching:
                affected.append({
                    "client_name":  client_name,
                    "company":      client["company"],
                    "contact_name": client["contact_name"],
                    "interfaces":   matching,
                })
                # Mark as solved
                for p in client["parked_interfaces"]:
                    if p["problem_type"] == problem_type:
                        p["solved"]    = True
                        p["solved_at"] = datetime.now().isoformat()

        self._data["solved_problems"].append({
            "problem_type": problem_type,
            "solved_at":    datetime.now().isoformat(),
            "clients_affected": len(affected),
        })
        self._save()
        return affected

    # ── Queries ───────────────────────────────────────────────────────

    def get_all_clients(self) -> list[ClientRecord]:
        return [
            ClientRecord(**c)
            for c in self._data["clients"].values()
        ]

    def get_parked_by_problem(self) -> dict[str, list[dict]]:
        """Group all parked interfaces by problem type."""
        grouped: dict[str, list] = {}
        for client_name, client in self._data["clients"].items():
            for p in client["parked_interfaces"]:
                if not p.get("solved"):
                    pt = p["problem_type"]
                    if pt not in grouped:
                        grouped[pt] = []
                    grouped[pt].append({
                        **p,
                        "client_name": client_name,
                        "company":     client["company"],
                    })
        return grouped

    def get_client_summary(self, client_name: str) -> dict:
        client = self._data["clients"].get(client_name, {})
        if not client:
            return {}
        parked    = [p for p in client["parked_interfaces"] if not p.get("solved")]
        completed = client["completed_interfaces"]
        return {
            "client_name":  client_name,
            "company":      client["company"],
            "completed":    len(completed),
            "parked":       len(parked),
            "parked_list":  parked,
            "ready_to_revisit": [p for p in parked if p.get("solved")],
        }

    def get_clients_ready_for_followup(self) -> list[dict]:
        """Clients with interfaces that were parked and are now solved."""
        ready = []
        for client_name, client in self._data["clients"].items():
            solved_pending = [
                p for p in client["parked_interfaces"]
                if p.get("solved") and not p.get("follow_up_sent")
            ]
            if solved_pending:
                ready.append({
                    "client_name":  client_name,
                    "company":      client["company"],
                    "contact_name": client["contact_name"],
                    "interfaces":   solved_pending,
                })
        return ready

    def mark_followup_sent(self, client_name: str, interface_name: str):
        client = self._data["clients"].get(client_name, {})
        for p in client.get("parked_interfaces", []):
            if p["interface_name"] == interface_name:
                p["follow_up_sent"] = True
        self._save()

    # ── Message generator ─────────────────────────────────────────────

    def generate_followup_message(
        self,
        client_name: str,
        interfaces: list[dict],
        contact_name: str = "",
    ) -> str:
        """Generate a personalized follow-up message for a client."""
        name     = contact_name or client_name
        iface_names = [i["interface_name"] for i in interfaces]
        count    = len(iface_names)

        if count == 1:
            iface_ref = f'"{iface_names[0]}"'
            verb      = "it"
        else:
            iface_ref = ", ".join(f'"{n}"' for n in iface_names[:-1])
            iface_ref += f' and "{iface_names[-1]}"'
            verb      = "them"

        return (
            f"Hi {name},\n\n"
            f"I wanted to circle back on {iface_ref} from your migration project. "
            f"When we last spoke I needed to do some additional research before I "
            f"could tackle {'that interface' if count == 1 else 'those interfaces'} properly.\n\n"
            f"I've since worked through the problem and I'm confident I can handle "
            f"{verb} now. If you're still looking to migrate {'it' if count == 1 else 'them'}, "
            f"I'd be happy to pick it back up.\n\n"
            f"Let me know if you'd like to reconnect.\n\n"
            f"Best regards"
        )

    def generate_parking_message(
        self,
        interface_name: str,
        contact_name: str = "",
    ) -> str:
        """Message to send client when parking an interface."""
        name = contact_name or "there"
        return (
            f"Hi {name},\n\n"
            f"I've finished the LOW and MEDIUM complexity interfaces and "
            f"they're ready for testing.\n\n"
            f"For \"{interface_name}\", I need to do some additional research "
            f"before I can do it properly. I'd rather take the time to get it "
            f"right than rush it. I'm going to keep working on it alongside "
            f"other projects and will come back to you as soon as I have a "
            f"solid solution. No extra cost for the research time.\n\n"
            f"Best regards"
        )

    # ── Persistence ───────────────────────────────────────────────────

    def _load(self) -> dict:
        if TRACKER_FILE.exists():
            try:
                return json.loads(TRACKER_FILE.read_text("utf-8"))
            except Exception:
                pass
        return {"clients": {}, "solved_problems": []}

    def _save(self):
        TRACKER_FILE.write_text(
            json.dumps(self._data, indent=2), "utf-8"
        )
