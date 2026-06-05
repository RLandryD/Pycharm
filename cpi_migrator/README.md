# SAP PI/PO → CPI Migration Scaffolder

Automates the assessment and iFlow scaffolding for migrating SAP PI/PO interfaces to SAP Cloud Integration (CPI), supporting both **Cloud Foundry (BTP)** and **Neo** environments.

## What it does

1. **Extracts** interface inventory from SAP PI/PO via REST API or ESR/ID export
2. **Analyzes** each interface for migration complexity (adapter types, mappings, channels)
3. **Scaffolds** CPI iFlow XML stubs ready for import
4. **Generates** a gap analysis report (Excel + Markdown)

## Project structure

```
cpi_migrator/
├── config/
│   └── settings.yaml          # Tenant credentials & options
├── auth/
│   └── authenticator.py       # OAuth2 (CF) + Basic (Neo) handlers
├── extractor/
│   └── pi_extractor.py        # Pull interfaces from PI/PO REST API
├── analyzer/
│   └── complexity_analyzer.py # Score & classify each interface
├── scaffolder/
│   └── iflow_scaffolder.py    # Generate iFlow XML from templates
├── reporter/
│   └── report_generator.py    # Excel gap report + Markdown summary
├── templates/
│   ├── iflow_base.xml.j2      # Jinja2 iFlow XML template
│   └── report_template.md.j2  # Markdown report template
├── tests/
│   └── test_all.py            # Unit tests with mock data
├── main.py                    # CLI entry point
└── requirements.txt
```

## Quick start

```bash
pip install -r requirements.txt
cp config/settings.yaml.example config/settings.yaml
# Edit settings.yaml with your credentials
python main.py --env cf --output ./output
```

## Output

- `output/iflows/` — one `.iflw` XML stub per interface, ready to import
- `output/gap_analysis.xlsx` — Excel with complexity scores and effort estimates
- `output/migration_report.md` — Markdown summary for stakeholders
