"""
layer3/message_engine.py — Message template generation and email sending.

Templates per role type × language (en / es / de).
Uses SMTP (Gmail / Outlook / any provider) or optionally SendGrid.
Logs every send to email_log table.

Setup:
    Copy config.example.env to config.env and fill in your credentials.

Run:
    python layer3/message_engine.py template --contact-id 3
    python layer3/message_engine.py send --contact-id 3 --lang en
    python layer3/message_engine.py log
    python layer3/message_engine.py log --contact-id 3
"""

import argparse
import os
import smtplib
import sys
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from database import get_connection, init_db

# ─────────────────────────────────────────────────────────────
# CONFIG — load from environment or config.env file
# ─────────────────────────────────────────────────────────────

def load_config():
    """Load email config from config.env file or environment variables."""
    config_file = Path(__file__).parent.parent / "config.env"
    config = {}
    if config_file.exists():
        for line in config_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    # Environment overrides file
    for key in ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS",
                "SENDER_NAME", "SENDER_EMAIL", "CV_PATH", "SENDGRID_KEY"]:
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


# ─────────────────────────────────────────────────────────────
# YOUR PROFESSIONAL PROFILE (used in templates)
# ─────────────────────────────────────────────────────────────

MY_PROFILE = {
    "name": "Your Name",           # ← change this
    "title": "SAP CPI/BTP Integration Consultant",
    "experience_years": 3,
    "certifications": "SAP Integration Suite Certified (2023 & 2025)",
    "company": "Deloitte",
    "languages": "native Spanish, C1 English, A2 German",
    "location": "México",
    "linkedin": "https://linkedin.com/in/yourprofile",  # ← change this
}


# ─────────────────────────────────────────────────────────────
# TEMPLATES
# ─────────────────────────────────────────────────────────────

TEMPLATES = {
    # ── SAP Manager / Director ──────────────────────────────
    "sap_manager": {
        "en": {
            "subject": "SAP CPI/BTP Integration Consultant — Open to New Opportunities",
            "body": """Hi {first_name},

I came across your profile and noticed your work leading SAP initiatives at {company}. I wanted to reach out as someone who might complement your team's efforts.

I'm an SAP CPI/BTP Integration Consultant with {experience_years} years of experience — currently at {my_company}, where I've delivered integration projects for Fortune 500 clients using SAP Integration Suite. I hold dual SAP certifications (2023 & 2025) and work fluently in both English and Spanish.

I'm actively exploring new opportunities, with a strong interest in roles at companies doing meaningful SAP work — either fully remote or based in Europe/Canada with visa support.

Would you be open to a brief 15-minute conversation? I'd love to learn more about what you're building at {company}.

Best regards,
{my_name}
{my_title}
{my_linkedin}
""",
        },
        "es": {
            "subject": "Consultor SAP CPI/BTP — Abierto a Nuevas Oportunidades",
            "body": """Hola {first_name},

Encontré tu perfil y me llamó la atención tu trayectoria liderando proyectos SAP en {company}. Quería ponerme en contacto porque creo que podría complementar bien al equipo.

Soy Consultor de SAP CPI/BTP con {experience_years} años de experiencia — actualmente en {my_company}, donde he liderado proyectos de integración para clientes Fortune 500 con SAP Integration Suite. Cuento con doble certificación SAP (2023 y 2025) y trabajo con fluidez en español e inglés.

Estoy explorando nuevas oportunidades, con interés en roles completamente remotos o en Europa/Canadá con apoyo de visa.

¿Estarías disponible para una charla breve de 15 minutos? Me gustaría conocer más sobre lo que están construyendo en {company}.

Saludos,
{my_name}
{my_title}
{my_linkedin}
""",
        },
        "de": {
            "subject": "SAP CPI/BTP Integration Consultant — Offen für neue Möglichkeiten",
            "body": """Hallo {first_name},

ich bin auf Ihr Profil gestoßen und war beeindruckt von Ihrer Arbeit mit SAP-Projekten bei {company}. Ich möchte mich gerne vorstellen, da ich glaube, Ihr Team gut ergänzen zu können.

Ich bin SAP CPI/BTP Integration Consultant mit {experience_years} Jahren Erfahrung — derzeit bei {my_company}, wo ich Integrationsprojekte für Fortune-500-Kunden mit SAP Integration Suite umgesetzt habe. Ich besitze zwei SAP-Zertifizierungen (2023 & 2025) und arbeite fließend auf Englisch und Spanisch.

Ich bin aktiv auf der Suche nach neuen Möglichkeiten — bevorzugt vollständig remote oder in Europa/Kanada mit Visa-Unterstützung.

Wären Sie offen für ein kurzes Gespräch von 15 Minuten? Ich würde gerne mehr über Ihre Arbeit bei {company} erfahren.

Mit freundlichen Grüßen,
{my_name}
{my_title}
{my_linkedin}
""",
        },
    },

    # ── Talent Acquisition ──────────────────────────────────
    "talent_acq": {
        "en": {
            "subject": "SAP CPI/BTP Consultant — {experience_years}y exp, Certified, Open to Opportunities",
            "body": """Hi {first_name},

I hope this message finds you well. I'm reaching out because I noticed {company} has been active in the SAP space, and I'd love to be on your radar for current or upcoming roles.

Quick overview:
• {experience_years} years as SAP CPI/BTP Integration Consultant (currently at Deloitte)
• Fortune 500 client experience with SAP Integration Suite
• SAP Integration Suite Certified — 2023 & 2025
• C1 English | Native Spanish | A2 German
• Open to remote worldwide or relocation to Europe/Canada with visa support

I'd be happy to share my CV or jump on a quick call. Is there a good way to connect?

Best,
{my_name}
{my_linkedin}
""",
        },
        "es": {
            "subject": "Consultor SAP CPI/BTP — {experience_years} años exp., Certificado",
            "body": """Hola {first_name},

Espero que estés bien. Me pongo en contacto porque noté que {company} tiene presencia activa en el ecosistema SAP y me gustaría ser una opción para roles actuales o futuros.

Un resumen rápido:
• {experience_years} años como Consultor SAP CPI/BTP (actualmente en Deloitte)
• Experiencia con clientes Fortune 500 en SAP Integration Suite
• Certificado SAP Integration Suite — 2023 y 2025
• Inglés C1 | Español nativo | Alemán A2
• Disponible para trabajo remoto mundial o reubicación a Europa/Canadá con apoyo de visa

Con gusto comparto mi CV o puedo agendar una llamada rápida.

Saludos,
{my_name}
{my_linkedin}
""",
        },
        "de": {
            "subject": "SAP CPI/BTP Consultant — {experience_years} J. Erfahrung, Zertifiziert",
            "body": """Hallo {first_name},

ich hoffe, es geht Ihnen gut. Ich wende mich an Sie, weil ich gesehen habe, dass {company} im SAP-Bereich aktiv ist — ich würde mich gerne für aktuelle oder zukünftige Positionen empfehlen.

Kurz zu meiner Person:
• {experience_years} Jahre als SAP CPI/BTP Integration Consultant (aktuell bei Deloitte)
• Projekterfahrung mit Fortune-500-Kunden in SAP Integration Suite
• SAP Integration Suite Zertifizierung — 2023 & 2025
• Englisch C1 | Spanisch Muttersprache | Deutsch A2
• Offen für Remote weltweit oder Umzug nach Europa/Kanada mit Visa-Unterstützung

Ich freue mich, meinen Lebenslauf zu teilen oder kurz zu telefonieren.

Mit freundlichen Grüßen,
{my_name}
{my_linkedin}
""",
        },
    },

    # ── Hiring Manager / HR Director ────────────────────────
    "hiring_manager": {
        "en": {
            "subject": "Experienced SAP Integration Consultant — Exploring Opportunities at {company}",
            "body": """Hi {first_name},

I'm an SAP CPI/BTP Integration Consultant with {experience_years} years of experience at Deloitte, delivering projects for Fortune 500 clients. I came across {company} and was impressed by your work — I wanted to reach out directly.

My background includes end-to-end SAP integration implementations, dual certification in SAP Integration Suite, and experience working in English and Spanish across international teams.

I'm currently exploring my next step, with a preference for roles that offer remote flexibility or support relocation to Europe/Canada.

I'd love to learn whether there are current or upcoming opportunities that could be a fit. Happy to share my CV on request.

Warm regards,
{my_name}
{my_title}
{my_linkedin}
""",
        },
        "es": {
            "subject": "Consultor SAP Integration con experiencia — Interesado en {company}",
            "body": """Hola {first_name},

Soy Consultor SAP CPI/BTP con {experience_years} años de experiencia en Deloitte, trabajando con clientes Fortune 500. Conocí a {company} y me generó mucho interés — me animé a escribirte directamente.

Tengo experiencia en implementaciones de integración SAP de punta a punta, doble certificación en SAP Integration Suite y trabajo en equipos internacionales en español e inglés.

Estoy explorando mi próximo paso, con preferencia por roles remotos o con apoyo de reubicación a Europa/Canadá.

¿Hay oportunidades actuales o próximas que puedan encajar? Con gusto comparto mi CV.

Saludos,
{my_name}
{my_title}
{my_linkedin}
""",
        },
        "de": {
            "subject": "Erfahrener SAP Integration Consultant — Interesse an {company}",
            "body": """Hallo {first_name},

ich bin SAP CPI/BTP Integration Consultant mit {experience_years} Jahren Erfahrung bei Deloitte, wo ich Projekte für Fortune-500-Kunden umgesetzt habe. {company} hat mein Interesse geweckt — deshalb melde ich mich direkt bei Ihnen.

Ich bringe Erfahrung in SAP-Integrationsprojekten von A bis Z mit, verfüge über eine doppelte SAP-Zertifizierung und arbeite auf Englisch und Spanisch in internationalen Teams.

Ich suche aktuell nach meiner nächsten Herausforderung — bevorzugt remote oder mit Umzugsunterstützung nach Europa/Kanada.

Gibt es aktuelle oder zukünftige Positionen, die passen könnten? Ich teile meinen Lebenslauf gerne auf Anfrage.

Herzliche Grüße,
{my_name}
{my_title}
{my_linkedin}
""",
        },
    },

    # ── IT Director / VP Technology ─────────────────────────
    "it_director": {
        "en": {
            "subject": "SAP Integration Expertise — Open to Opportunities at {company}",
            "body": """Hi {first_name},

I noticed your leadership role in technology at {company} and wanted to connect around SAP integration.

I'm an SAP CPI/BTP Consultant with {experience_years} years of experience building integration architectures on SAP Integration Suite — currently at Deloitte, supporting Fortune 500 transformations. I'm certified in SAP Integration Suite (2023 & 2025) and experienced in both agile and enterprise delivery models.

If {company} is working on SAP-related initiatives or scaling integration capabilities, I'd be glad to explore how I might contribute — either as an employee or contractor.

Happy to have a brief conversation if this is relevant to your roadmap.

Best,
{my_name}
{my_linkedin}
""",
        },
        "es": {
            "subject": "Experiencia SAP Integration — Interés en oportunidades en {company}",
            "body": """Hola {first_name},

Vi tu rol como líder tecnológico en {company} y quería conectar por el tema de integración SAP.

Soy Consultor SAP CPI/BTP con {experience_years} años construyendo arquitecturas de integración en SAP Integration Suite — actualmente en Deloitte, apoyando transformaciones Fortune 500. Cuento con certificación SAP (2023 y 2025) y experiencia en modelos ágiles y enterprise.

Si {company} tiene iniciativas SAP o está escalando capacidades de integración, con gusto exploramos cómo puedo contribuir, ya sea como empleado o contratista.

¿Te parece bien una charla breve?

Saludos,
{my_name}
{my_linkedin}
""",
        },
        "de": {
            "subject": "SAP Integration Know-how — Interesse an Möglichkeiten bei {company}",
            "body": """Hallo {first_name},

ich habe Ihre leitende Rolle im Technologiebereich bei {company} gesehen und möchte mich im Kontext SAP-Integration gerne vorstellen.

Ich bin SAP CPI/BTP Consultant mit {experience_years} Jahren Erfahrung im Aufbau von Integrationsarchitekturen auf SAP Integration Suite — aktuell bei Deloitte, wo ich Fortune-500-Transformationen begleite. Ich bin SAP-zertifiziert (2023 & 2025) und habe Erfahrung mit agilen und Enterprise-Delivery-Modellen.

Falls {company} an SAP-Initiativen arbeitet oder Integrationskapazitäten ausbaut, würde ich gerne erkunden, wie ich beitragen kann — als Mitarbeiter oder Auftragnehmer.

Wären Sie offen für ein kurzes Gespräch?

Mit freundlichen Grüßen,
{my_name}
{my_linkedin}
""",
        },
    },

    # ── SAP Practice Lead / CoE ─────────────────────────────
    "sap_practice": {
        "en": {
            "subject": "SAP CPI/BTP Consultant Interested in Joining {company}'s SAP Practice",
            "body": """Hi {first_name},

I've been following {company}'s SAP practice and the work your team is doing — it's exactly the kind of environment I'm looking for in my next role.

I'm an SAP CPI/BTP Integration Consultant with {experience_years} years of hands-on experience at Deloitte, currently working with Fortune 500 clients on SAP Integration Suite. I hold certifications from both 2023 and 2025, and I stay closely engaged with SAP BTP's evolving landscape.

I'm exploring opportunities to join a strong SAP practice where I can grow technically while contributing meaningfully. I'm open to remote or hybrid roles, and to relocation in Europe/Canada with visa support.

Would you be open to a quick conversation about what your practice looks like today?

Best,
{my_name}
{my_title}
{my_linkedin}
""",
        },
        "es": {
            "subject": "Consultor SAP CPI/BTP interesado en la práctica SAP de {company}",
            "body": """Hola {first_name},

He estado siguiendo la práctica SAP de {company} y el trabajo de tu equipo — es exactamente el tipo de entorno que busco en mi próxima etapa.

Soy Consultor SAP CPI/BTP con {experience_years} años de experiencia práctica en Deloitte, trabajando con clientes Fortune 500 en SAP Integration Suite. Cuento con certificaciones de 2023 y 2025 y mantengo un seguimiento activo del ecosistema SAP BTP.

Busco unirme a una práctica SAP sólida donde pueda crecer técnicamente y aportar valor. Estoy abierto a roles remotos o híbridos, y a reubicación en Europa/Canadá con apoyo de visa.

¿Te parece bien una charla rápida sobre cómo está hoy la práctica?

Saludos,
{my_name}
{my_title}
{my_linkedin}
""",
        },
        "de": {
            "subject": "SAP CPI/BTP Consultant — Interesse an der SAP Practice von {company}",
            "body": """Hallo {first_name},

ich verfolge die SAP-Praxis von {company} und die Arbeit Ihres Teams — genau das ist das Umfeld, das ich für meinen nächsten Schritt suche.

Ich bin SAP CPI/BTP Integration Consultant mit {experience_years} Jahren praktischer Erfahrung bei Deloitte, wo ich Fortune-500-Kunden auf SAP Integration Suite betreue. Ich bin SAP-zertifiziert (2023 & 2025) und verfolge aktiv die Entwicklungen rund um SAP BTP.

Ich suche nach einer starken SAP-Praxis, in der ich mich technisch weiterentwickeln und gleichzeitig einen echten Beitrag leisten kann. Offen für remote/hybrid oder Umzug nach Europa/Kanada.

Wären Sie offen für ein kurzes Gespräch über Ihre aktuelle Praxis?

Mit freundlichen Grüßen,
{my_name}
{my_title}
{my_linkedin}
""",
        },
    },

    # ── Generic / Other ─────────────────────────────────────
    "other": {
        "en": {
            "subject": "SAP CPI/BTP Consultant — Open to Opportunities",
            "body": """Hi {first_name},

I hope this message finds you well. I'm an SAP CPI/BTP Integration Consultant with {experience_years} years of experience — currently at Deloitte — and I'm actively exploring new opportunities.

I specialize in SAP Integration Suite implementations, hold dual SAP certifications (2023 & 2025), and have experience working with Fortune 500 clients in international settings. I'm open to remote roles worldwide or relocation to Europe/Canada with visa support.

I'd love to connect and learn more about {company}. Would a brief conversation be possible?

Best regards,
{my_name}
{my_linkedin}
""",
        },
        "es": {
            "subject": "Consultor SAP CPI/BTP — Abierto a Oportunidades",
            "body": """Hola {first_name},

Espero que estés bien. Soy Consultor SAP CPI/BTP con {experience_years} años de experiencia — actualmente en Deloitte — y estoy explorando activamente nuevas oportunidades.

Me especializo en implementaciones de SAP Integration Suite, cuento con doble certificación SAP (2023 y 2025) y experiencia con clientes Fortune 500 en contextos internacionales. Estoy abierto a trabajo remoto o reubicación en Europa/Canadá.

Me encantaría conectar y conocer más sobre {company}.

Saludos,
{my_name}
{my_linkedin}
""",
        },
        "de": {
            "subject": "SAP CPI/BTP Consultant — Offen für neue Möglichkeiten",
            "body": """Hallo {first_name},

ich bin SAP CPI/BTP Integration Consultant mit {experience_years} Jahren Erfahrung — aktuell bei Deloitte — und suche aktiv nach neuen Möglichkeiten.

Ich spezialisiere mich auf SAP Integration Suite, verfüge über zwei SAP-Zertifizierungen (2023 & 2025) und habe Erfahrung mit Fortune-500-Kunden in internationalen Projekten. Ich bin offen für Remote-Stellen weltweit oder Umzug nach Europa/Kanada.

Ich würde mich gerne mit Ihnen vernetzen und mehr über {company} erfahren.

Mit freundlichen Grüßen,
{my_name}
{my_linkedin}
""",
        },
    },
}


# ─────────────────────────────────────────────────────────────
# TEMPLATE RENDERING
# ─────────────────────────────────────────────────────────────

def render_template(contact: dict, lang: str = None) -> dict:
    """Render subject + body for a contact using their role category and language."""
    role = contact["role_category"] or "other"
    if role not in TEMPLATES:
        role = "other"

    lang = lang or contact.get("language_preference") or "en"
    if lang not in TEMPLATES[role]:
        lang = "en"

    tpl = TEMPLATES[role][lang]
    first_name = contact["full_name"].split()[0]

    placeholders = {
        "first_name": first_name,
        "company": contact.get("company_name") or "your company",
        "experience_years": MY_PROFILE["experience_years"],
        "my_name": MY_PROFILE["name"],
        "my_title": MY_PROFILE["title"],
        "my_company": MY_PROFILE["company"],
        "my_linkedin": MY_PROFILE["linkedin"],
    }

    return {
        "subject": tpl["subject"].format(**placeholders),
        "body": tpl["body"].format(**placeholders),
        "lang": lang,
        "role": role,
    }


def preview_template(contact_id: int, lang: str = None):
    """Print a rendered message preview for a contact."""
    with get_connection() as conn:
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        print(f"[Layer 3] Contact #{contact_id} not found.")
        return

    contact = dict(contact)
    rendered = render_template(contact, lang)

    print(f"\n{'═'*60}")
    print(f"  MESSAGE PREVIEW — {contact['full_name']} (#{contact_id})")
    print(f"{'═'*60}")
    print(f"  Language : {rendered['lang'].upper()}")
    print(f"  Role     : {rendered['role']}")
    print(f"  Subject  : {rendered['subject']}")
    print(f"{'─'*60}")
    print(rendered["body"])
    print(f"{'═'*60}\n")
    return rendered


# ─────────────────────────────────────────────────────────────
# EMAIL SENDING
# ─────────────────────────────────────────────────────────────

def send_email(contact_id: int, lang: str = None, attach_cv: bool = True,
               dry_run: bool = False):
    """Send email to a contact. Logs result to email_log table."""
    config = load_config()
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SENDER_EMAIL"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"[Layer 3] ⚠️  Missing config keys: {', '.join(missing)}")
        print("       Create a config.env file with your SMTP settings.")
        return False

    with get_connection() as conn:
        contact = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not contact:
        print(f"[Layer 3] Contact #{contact_id} not found.")
        return False
    contact = dict(contact)

    if not contact.get("email"):
        print(f"[Layer 3] Contact #{contact_id} has no email address.")
        return False

    rendered = render_template(contact, lang)

    msg = MIMEMultipart()
    msg["From"] = f"{config.get('SENDER_NAME', MY_PROFILE['name'])} <{config['SENDER_EMAIL']}>"
    msg["To"] = contact["email"]
    msg["Subject"] = rendered["subject"]
    msg.attach(MIMEText(rendered["body"], "plain", "utf-8"))

    # Attach CV if path configured and file exists
    cv_path = config.get("CV_PATH", "")
    if attach_cv and cv_path and Path(cv_path).exists():
        with open(cv_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=Path(cv_path).name)
            part["Content-Disposition"] = f'attachment; filename="{Path(cv_path).name}"'
            msg.attach(part)
        print(f"[Layer 3] CV attached: {Path(cv_path).name}")

    if dry_run:
        print(f"\n[Layer 3] DRY RUN — would send to: {contact['email']}")
        print(f"  Subject: {rendered['subject']}")
        print(f"  Body preview:\n{rendered['body'][:300]}...")
        return True

    status = "sent"
    error_msg = None
    try:
        port = int(config["SMTP_PORT"])
        if port == 465:
            server = smtplib.SMTP_SSL(config["SMTP_HOST"], port)
        else:
            server = smtplib.SMTP(config["SMTP_HOST"], port)
            server.ehlo()
            server.starttls()
        server.login(config["SMTP_USER"], config["SMTP_PASS"])
        server.sendmail(config["SENDER_EMAIL"], contact["email"], msg.as_string())
        server.quit()
        print(f"[Layer 3] ✅ Email sent to {contact['full_name']} <{contact['email']}>")
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        print(f"[Layer 3] ❌ Send failed: {e}")

    # Log to DB
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO email_log (contact_id, subject, body, status, template_used, error_msg)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (contact_id, rendered["subject"], rendered["body"],
              status, f"{rendered['role']}_{rendered['lang']}", error_msg))
        # Update contact last interaction if sent
        if status == "sent":
            from datetime import date
            conn.execute(
                "UPDATE contacts SET last_interaction=?, updated_at=datetime('now') WHERE id=?",
                (date.today().isoformat(), contact_id)
            )

    return status == "sent"


def show_email_log(contact_id: int = None, limit: int = 50):
    """Display email send log."""
    query = """
        SELECT l.id, l.sent_at, l.status, l.subject, l.template_used,
               c.full_name, c.email
        FROM email_log l
        LEFT JOIN contacts c ON c.id = l.contact_id
    """
    params = []
    if contact_id:
        query += " WHERE l.contact_id = ?"
        params.append(contact_id)
    query += f" ORDER BY l.sent_at DESC LIMIT {limit}"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    print(f"\n{'─'*100}")
    print(f"{'ID':<5} {'Sent At':<22} {'Status':<8} {'To':<25} {'Template':<20} {'Subject'}")
    print(f"{'─'*100}")
    for r in rows:
        status_icon = "✅" if r["status"] == "sent" else "❌"
        print(f"{r['id']:<5} {r['sent_at']:<22} {status_icon} {(r['email'] or ''):<25} "
              f"{(r['template_used'] or ''):<20} {(r['subject'] or '')[:40]}")
    print(f"{'─'*100}")
    print(f"Total: {len(rows)} email(s)\n")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(description="SAP Job Hunter — Layer 3: Message & Email Engine")
    sub = p.add_subparsers(dest="action", required=True)

    tpl = sub.add_parser("template", help="Preview message template for a contact")
    tpl.add_argument("--contact-id", type=int, required=True)
    tpl.add_argument("--lang", choices=["en", "es", "de"])

    send = sub.add_parser("send", help="Send email to a contact")
    send.add_argument("--contact-id", type=int, required=True)
    send.add_argument("--lang", choices=["en", "es", "de"])
    send.add_argument("--no-cv", action="store_true", help="Don't attach CV")
    send.add_argument("--dry-run", action="store_true", help="Preview without sending")

    log = sub.add_parser("log", help="Show email log")
    log.add_argument("--contact-id", type=int)
    log.add_argument("--limit", type=int, default=50)

    return p


def main():
    init_db()
    parser = build_parser()
    args = parser.parse_args()

    if args.action == "template":
        preview_template(args.contact_id, args.lang)
    elif args.action == "send":
        send_email(
            contact_id=args.contact_id,
            lang=args.lang,
            attach_cv=not args.no_cv,
            dry_run=args.dry_run,
        )
    elif args.action == "log":
        show_email_log(
            contact_id=getattr(args, "contact_id", None),
            limit=args.limit,
        )


if __name__ == "__main__":
    main()
