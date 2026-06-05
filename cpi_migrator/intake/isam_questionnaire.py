"""
intake/isam_questionnaire.py

10-question ISA-M assessment questionnaire that recommends the
optimal SAP BTP tool based on integration characteristics.

Recommendations:
  - Cloud Integration (CPI)     — A2A/B2B process integration
  - API Management              — API-led, developer-facing
  - Advanced Event Mesh (AEM)   — Event-driven, real-time
  - Edge Integration Cell (EIC) — On-premise/local processing
  - Combination                 — multiple tools needed
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ISAMAnswer:
    question_id: str
    answer: str


@dataclass
class ISAMRecommendation:
    primary_tool: str
    secondary_tools: list[str]
    isa_m_pattern: str           # A2A / B2B / API / Event / User / Thing
    integration_style: str       # Process / Data / User / Thing
    score_breakdown: dict[str, int]
    reasoning: list[str]
    confidence: float            # 0.0 - 1.0


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

QUESTIONS = [
    {
        "id": "Q1",
        "text": "What is the integration direction?",
        "options": [
            ("a2a",      "A2A — Application to Application (internal systems)"),
            ("b2b",      "B2B — Business to Business (external partners)"),
            ("api",      "API — Exposing or consuming APIs for developers/apps"),
            ("event",    "Event — Real-time event notifications between systems"),
        ],
    },
    {
        "id": "Q2",
        "text": "What is the processing mode?",
        "options": [
            ("sync",     "Synchronous — caller waits for response"),
            ("async",    "Asynchronous — fire and forget / queued"),
            ("batch",    "Batch — scheduled, high-volume transfers"),
            ("realtime", "Real-time event stream — continuous pub/sub"),
        ],
    },
    {
        "id": "Q3",
        "text": "What is the expected message volume?",
        "options": [
            ("low",      "Low — < 1,000 messages/day"),
            ("medium",   "Medium — 1,000 – 100,000 messages/day"),
            ("high",     "High — 100,000 – 1,000,000 messages/day"),
            ("extreme",  "Extreme — > 1,000,000 messages/day"),
        ],
    },
    {
        "id": "Q4",
        "text": "Where are the connected systems located?",
        "options": [
            ("cloud",    "All cloud — SaaS to SaaS or BTP to BTP"),
            ("hybrid",   "Hybrid — mix of cloud and on-premise"),
            ("onpremise","All on-premise — private network only"),
            ("edge",     "Edge/local — strict data residency, offline resilience needed"),
        ],
    },
    {
        "id": "Q5",
        "text": "What is the data sensitivity level?",
        "options": [
            ("public",   "Public — no restrictions"),
            ("internal", "Internal — standard enterprise data"),
            ("sensitive","Sensitive — HR, Finance, PII, GDPR scope"),
            ("critical", "Critical — Payroll, banking, healthcare, regulated"),
        ],
    },
    {
        "id": "Q6",
        "text": "Who consumes this integration?",
        "options": [
            ("system",   "System-to-system — no human in the loop"),
            ("developer","Developer / App — exposed as an API"),
            ("business", "Business user — workflow or approval"),
            ("partner",  "External partner — EDI, cXML, AS2"),
        ],
    },
    {
        "id": "Q7",
        "text": "Is message ordering (EOIO) required?",
        "options": [
            ("no",       "No — order does not matter"),
            ("partial",  "Partial — best effort ordering"),
            ("strict",   "Yes — Exactly Once In Order (EOIO) required"),
        ],
    },
    {
        "id": "Q8",
        "text": "Does this integration require complex message transformation?",
        "options": [
            ("none",     "None — pass-through or simple routing"),
            ("simple",   "Simple — field mapping, format conversion"),
            ("complex",  "Complex — multi-step mapping, enrichment, splitting"),
            ("extreme",  "Extreme — BPM orchestration, long-running processes"),
        ],
    },
    {
        "id": "Q9",
        "text": "Are there compliance or regulatory requirements?",
        "options": [
            ("none",     "None"),
            ("gdpr",     "GDPR / data privacy"),
            ("industry", "Industry-specific (HIPAA, PCI-DSS, SOX)"),
            ("government","Government / B2G (e-invoicing mandates, customs)"),
        ],
    },
    {
        "id": "Q10",
        "text": "What is the primary integration pattern?",
        "options": [
            ("process",  "Process integration — orchestrate business processes"),
            ("data",     "Data integration — sync master data between systems"),
            ("event",    "Event-driven — react to business events in real-time"),
            ("api_mgmt", "API management — manage, secure, monetize APIs"),
        ],
    },
]


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def evaluate(answers: list[ISAMAnswer]) -> ISAMRecommendation:
    """Score answers and return tool recommendation."""
    answer_map = {a.question_id: a.answer for a in answers}

    scores = {
        "Cloud Integration":  0,
        "API Management":     0,
        "Advanced Event Mesh": 0,
        "Edge Integration Cell": 0,
    }
    reasoning = []

    # Q1 — Direction
    q1 = answer_map.get("Q1", "")
    if q1 == "a2a":
        scores["Cloud Integration"]  += 4
        reasoning.append("A2A direction → Cloud Integration is the primary tool")
    elif q1 == "b2b":
        scores["Cloud Integration"]  += 4
        reasoning.append("B2B direction → Cloud Integration with Trading Partner Mgmt")
    elif q1 == "api":
        scores["API Management"]     += 5
        scores["Cloud Integration"]  += 1
        reasoning.append("API direction → API Management is the primary tool")
    elif q1 == "event":
        scores["Advanced Event Mesh"]  += 5
        reasoning.append("Event direction → Advanced Event Mesh for pub/sub")

    # Q2 — Processing mode
    q2 = answer_map.get("Q2", "")
    if q2 == "sync":
        scores["Cloud Integration"]  += 2
        scores["API Management"]     += 2
        reasoning.append("Synchronous → Cloud Integration or API Management")
    elif q2 in ("async", "batch"):
        scores["Cloud Integration"]  += 3
        reasoning.append("Async/Batch → Cloud Integration with JMS queues")
    elif q2 == "realtime":
        scores["Advanced Event Mesh"]  += 4
        reasoning.append("Real-time streams → Advanced Event Mesh")

    # Q3 — Volume
    q3 = answer_map.get("Q3", "")
    if q3 in ("high", "extreme"):
        scores["Advanced Event Mesh"]  += 2
        reasoning.append("High volume → consider AEM for throughput scaling")

    # Q4 — Location
    q4 = answer_map.get("Q4", "")
    if q4 == "onpremise":
        scores["Edge Integration Cell"] += 4
        reasoning.append("All on-premise → Edge Integration Cell for local processing")
    elif q4 == "edge":
        scores["Edge Integration Cell"] += 5
        reasoning.append("Edge/local required → Edge Integration Cell mandatory")
    elif q4 == "hybrid":
        scores["Cloud Integration"]  += 2
        scores["Edge Integration Cell"] += 1
        reasoning.append("Hybrid → Cloud Integration + Cloud Connector (SCC)")
    elif q4 == "cloud":
        scores["Cloud Integration"]  += 2
        scores["API Management"]     += 1

    # Q5 — Sensitivity
    q5 = answer_map.get("Q5", "")
    if q5 in ("sensitive", "critical"):
        reasoning.append("Sensitive data → enable Message-Level Security (MLS/PGP)")
        if q5 == "critical":
            scores["Edge Integration Cell"] += 1
            reasoning.append("Critical data → consider Edge Cell for data residency")

    # Q6 — Consumer
    q6 = answer_map.get("Q6", "")
    if q6 == "developer":
        scores["API Management"]     += 4
        reasoning.append("Developer consumer → API Management for portal + docs")
    elif q6 == "partner":
        scores["Cloud Integration"]  += 3
        reasoning.append("External partner → Cloud Integration with AS2/Trading Partners")
    elif q6 == "business":
        scores["Cloud Integration"]  += 2
        reasoning.append("Business user → Cloud Integration + BTP Workflow Service")

    # Q7 — Ordering
    q7 = answer_map.get("Q7", "")
    if q7 == "strict":
        scores["Cloud Integration"]  += 2
        reasoning.append("EOIO required → Cloud Integration with DataStore staging pattern")

    # Q8 — Transformation
    q8 = answer_map.get("Q8", "")
    if q8 in ("complex", "extreme"):
        scores["Cloud Integration"]  += 3
        reasoning.append("Complex transformation → Cloud Integration iFlow designer")
    elif q8 == "none":
        scores["API Management"]     += 1
        scores["Advanced Event Mesh"]  += 1

    # Q9 — Compliance
    q9 = answer_map.get("Q9", "")
    if q9 == "government":
        scores["Cloud Integration"]  += 2
        reasoning.append("B2G compliance → Cloud Integration with localized content")
    elif q9 in ("gdpr", "industry"):
        reasoning.append("Regulatory requirement → enable audit logging + data masking")

    # Q10 — Pattern
    q10 = answer_map.get("Q10", "")
    if q10 == "process":
        scores["Cloud Integration"]  += 3
        reasoning.append("Process integration pattern → Cloud Integration")
    elif q10 == "data":
        scores["Cloud Integration"]  += 2
        reasoning.append("Data integration pattern → Cloud Integration")
    elif q10 == "event":
        scores["Advanced Event Mesh"]  += 3
        reasoning.append("Event-driven pattern → Advanced Event Mesh")
    elif q10 == "api_mgmt":
        scores["API Management"]     += 4
        reasoning.append("API management pattern → API Management")

    # Determine primary + secondary
    sorted_tools  = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary       = sorted_tools[0][0]
    secondary     = [t for t, s in sorted_tools[1:] if s >= sorted_tools[0][1] * 0.5]

    # ISA-M pattern
    isa_m_map = {
        "a2a":  "A2A Process Integration",
        "b2b":  "B2B Partner Integration",
        "api":  "API-Managed Integration",
        "event":"Event-Based Integration",
    }
    isa_m_pattern = isa_m_map.get(answer_map.get("Q1", ""), "A2A Process Integration")

    style_map = {
        "process":  "Process Integration",
        "data":     "Data Integration",
        "event":    "Event Integration",
        "api_mgmt": "API Integration",
    }
    integration_style = style_map.get(answer_map.get("Q10", ""), "Process Integration")

    total  = sum(scores.values()) or 1
    confidence = sorted_tools[0][1] / total

    return ISAMRecommendation(
        primary_tool=primary,
        secondary_tools=secondary,
        isa_m_pattern=isa_m_pattern,
        integration_style=integration_style,
        score_breakdown=scores,
        reasoning=reasoning,
        confidence=min(confidence * 2, 1.0),
    )


def get_questions() -> list[dict]:
    return QUESTIONS
