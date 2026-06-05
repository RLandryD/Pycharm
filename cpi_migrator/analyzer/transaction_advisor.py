"""
analyzer/transaction_advisor.py

Recommends the iFlow transaction-handling setting per interface.

CPI's Integration Process step exposes a "Transaction Handling" attribute with
three values: Required, Requires New, Not Supported. Picking the wrong one is
a frequent post-deploy bug; the right value is deterministic from two facts:

  1. JDBC presence       — JDBC writes must participate in a transaction.
  2. EOIO / sequential   — Exactly-Once-In-Order processing needs a new
                           transaction boundary per message to enforce order.

Rules (deliberately conservative — wrong defaults cause silent data loss):
  - JDBC writer + EOIO            → Requires New
  - JDBC writer, no EOIO          → Required
  - EOIO, no JDBC                 → Required
  - Read-only / no JDBC, no EOIO  → Not Supported (default; lighter overhead)

Read-only: classifies an InterfaceRecord / config snapshot and returns a
TransactionAdvisory record. Pre-flight and the JDBC sheet consume it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TransactionAdvisory:
    interface_name: str
    handling: str               # "Required" | "Requires New" | "Not Supported"
    reasoning: str
    has_jdbc: bool
    has_eoio: bool


def _detect_eoio(record, config=None) -> bool:
    """EOIO is signalled by adapter type (XI/JMS) or by an explicit
    sequential/QoS marker on the config. Conservative — any signal counts."""
    sender = (getattr(record, "sender_adapter", "") or "").upper()
    receiver = (getattr(record, "receiver_adapter", "") or "").upper()
    if "XI" in sender or "XI" in receiver:
        return True
    if "JMS" in sender or "JMS" in receiver:
        return True
    if config is not None:
        rt = getattr(config, "runtime", None)
        if rt and "exactly once" in (getattr(rt, "quality_of_service", "") or "").lower():
            return True
        # Some workflows set a sequential marker on the message section
        msg = getattr(config, "message", None)
        if msg and getattr(msg, "is_async", False) and "JMS" in (sender + receiver):
            return True
    return False


def _detect_jdbc(record, config=None) -> bool:
    """JDBC presence on either side, OR a JDBC URL set on the message config."""
    if "JDBC" in (getattr(record, "sender_adapter", "") or "").upper():
        return True
    if "JDBC" in (getattr(record, "receiver_adapter", "") or "").upper():
        return True
    if config is not None:
        msg = getattr(config, "message", None)
        if msg and (getattr(msg, "jdbc_jndi", "") or getattr(msg, "jdbc_driver", "")):
            return True
    return False


def advise(record, config=None) -> TransactionAdvisory:
    """Return the transaction-handling advisory for one interface."""
    has_jdbc = _detect_jdbc(record, config)
    has_eoio = _detect_eoio(record, config)
    name = getattr(record, "name", "") or "?"

    if has_jdbc and has_eoio:
        return TransactionAdvisory(
            interface_name=name, handling="Requires New", has_jdbc=True, has_eoio=True,
            reasoning="JDBC write under EOIO: each message needs its own transaction "
                     "boundary to enforce sequential order without holding locks.")
    if has_jdbc:
        return TransactionAdvisory(
            interface_name=name, handling="Required", has_jdbc=True, has_eoio=False,
            reasoning="JDBC writer present: enroll in the surrounding transaction so "
                     "DB writes commit/rollback atomically with the iFlow.")
    if has_eoio:
        return TransactionAdvisory(
            interface_name=name, handling="Required", has_jdbc=False, has_eoio=True,
            reasoning="EOIO / sequential delivery: transaction context required to "
                     "preserve order through the pipeline.")
    return TransactionAdvisory(
        interface_name=name, handling="Not Supported", has_jdbc=False, has_eoio=False,
        reasoning="No JDBC writes and no ordering constraint — running without a "
                 "transaction reduces overhead and contention.")


def advise_all(records: list, configs: dict = None) -> list[TransactionAdvisory]:
    configs = configs or {}
    return [advise(r, configs.get(getattr(r, "name", ""))) for r in records]


def advisories_to_preflight_items(advisories: list[TransactionAdvisory]) -> list:
    """Surface non-default advisories as pre-flight items so the consultant
    can confirm them before deployment. 'Not Supported' is the safe default
    and produces no item."""
    if not advisories:
        return []
    from reporter.preflight_generator import PreflightItem
    items = []
    for adv in advisories:
        if adv.handling == "Not Supported":
            continue
        items.append(PreflightItem(
            category="Transaction Handling",
            task=f"Set iFlow transaction handling = {adv.handling} for {adv.interface_name}",
            detail=adv.reasoning + " Configure on the Integration Process shape "
                                   "in the CPI iFlow editor.",
            responsible="Consultant",
            mandatory=True,
            triggered_by=("JDBC + EOIO" if adv.has_jdbc and adv.has_eoio
                          else "JDBC writer" if adv.has_jdbc else "EOIO / sequential"),
        ))
    return items
