#!/usr/bin/env python3
"""
main.py — SAP Job Hunter: unified entry point.

Usage:
    python main.py layer1 seed
    python main.py layer1 list --remote
    python main.py layer1 add --name "REWE Group" --hq DE --remote hybrid
    python main.py layer1 export

    python main.py layer2 add --name "Anna Schmidt" --title "SAP Manager" \\
                              --company "Nagarro" --country DE --lang de \\
                              --role sap_manager --priority 1

    python main.py layer2 followup
    python main.py layer2 list --due-today
    python main.py layer2 update --id 1 --status connected

    python main.py layer3 template --contact-id 1 --lang de
    python main.py layer3 send --contact-id 1 --dry-run
    python main.py layer3 log

    python main.py layer4 generate --contact-id 1
    python main.py layer4 generate-from-csv --file data/contacts_export.csv --id 3
    python main.py layer4 list-drafts
"""

import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    layer = sys.argv[1]
    remaining = sys.argv[2:]

    if layer == "layer1":
        sys.argv = ["layer1/company_db.py"] + remaining
        from layer1.company_db import main as run
    elif layer == "layer2":
        sys.argv = ["layer2/contact_tracker.py"] + remaining
        from layer2.contact_tracker import main as run
    elif layer == "layer3":
        sys.argv = ["layer3/message_engine.py"] + remaining
        from layer3.message_engine import main as run
    elif layer == "layer4":
        sys.argv = ["layer4/personalized_message.py"] + remaining
        from layer4.personalized_message import main as run
    elif layer == "init":
        from database import init_db
        init_db()
        return
    else:
        print(f"Unknown layer: {layer}. Choose: layer1, layer2, layer3, layer4, init")
        sys.exit(1)

    run()


if __name__ == "__main__":
    main()
