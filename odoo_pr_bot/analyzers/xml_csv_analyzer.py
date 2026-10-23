"""Analizzatore per viste/dati XML Odoo (18.0, 19.0, 20.0) e file ir.model.access.csv."""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET

from odoo_pr_bot.analyzers.manifest_analyzer import extract_module_name_from_path
from odoo_pr_bot.models import ReviewFinding, Severity

EXPECTED_ACL_HEADERS = [
    "id",
    "name",
    "model_id:id",
    "group_id:id",
    "perm_read",
    "perm_write",
    "perm_create",
    "perm_unlink",
]

VIEW_MODE_TREE_RE = re.compile(
    r"""<field\s+name=['"]view_mode['"]\s*>[^<]*\btree\b[^<]*</field>"""
)


def analyze_xml_file(
    file_path: str,
    content: str,
    target_odoo_series: str = "18.0",
) -> list[ReviewFinding]:
    """Analizza un file XML di un modulo Odoo per le serie 18.0, 19.0 e 20.0."""
    findings: list[ReviewFinding] = []
    mod_name, _ = extract_module_name_from_path(file_path)

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        line_num = exc.position[0] if hasattr(exc, "position") else 1
        findings.append(
            ReviewFinding(
                rule_id="ODOO-X001",
                severity=Severity.ERROR,
                file_path=file_path,
                line=line_num,
                message=f"File XML malformato: {exc}",
                module_name=mod_name,
            )
        )
        return findings

    # 1. Tag radice <openerp> deprecato
    if root.tag == "openerp":
        findings.append(
            ReviewFinding(
                rule_id="ODOO-X002",
                severity=Severity.ERROR,
                file_path=file_path,
                line=1,
                message="Il tag radice '<openerp>' non è valido in Odoo 18.0/19.0/20.0. Usa '<odoo>'.",
                suggestion="<odoo>",
                module_name=mod_name,
            )
        )

    try:
        major_series = int(target_odoo_series.split(".")[0])
    except ValueError:
        major_series = 18

    lines = content.splitlines()

    for idx, line in enumerate(lines, start=1):
        # 2. Controllo attributi rimossi in Odoo 17/18/19/20 (attrs="..." e states="...")
        if major_series >= 17 and ("attrs=" in line or "states=" in line):
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-X003",
                    severity=Severity.ERROR,
                    file_path=file_path,
                    line=idx,
                    message=(
                        f"In Odoo {major_series}.0 gli attributi XML 'attrs' e 'states' non sono supportati. "
                        "Usa espressioni dirette su 'invisible', 'readonly', 'required' o 'column_invisible'."
                    ),
                    module_name=mod_name,
                )
            )

        # 3. Controllo <tree> sostituito da <list> in Odoo 18.0, 19.0, 20.0
        if major_series >= 18 and re.search(r"<tree(\s|>)", line):
            sev = Severity.ERROR if major_series >= 19 else Severity.WARNING
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-X004",
                    severity=sev,
                    file_path=file_path,
                    line=idx,
                    message=(
                        f"In Odoo {major_series}.0 il tag vista '<tree>' è stato sostituito da '<list>'. "
                        "Aggiorna '<tree>...</tree>' in '<list>...</list>'."
                    ),
                    suggestion=line.replace("<tree", "<list").strip(),
                    module_name=mod_name,
                )
            )

        # 4. Controllo view_mode contenente 'tree' invece di 'list' in Odoo 18.0, 19.0, 20.0
        if major_series >= 18 and VIEW_MODE_TREE_RE.search(line):
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-X005",
                    severity=Severity.WARNING,
                    file_path=file_path,
                    line=idx,
                    message=(
                        f"In Odoo {major_series}.0 'view_mode' usa 'list' al posto di 'tree' "
                        "(es. `<field name=\"view_mode\">list,form</field>`)."
                    ),
                    module_name=mod_name,
                )
            )

        # 5. Controllo legacy <div class="oe_chatter"> invece di <chatter/> in Odoo 18.0, 19.0, 20.0
        if major_series >= 18 and "oe_chatter" in line:
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-X006",
                    severity=Severity.INFO,
                    file_path=file_path,
                    line=idx,
                    message=(
                        f"In Odoo {major_series}.0 il blocco '<div class=\"oe_chatter\">' è sostituito "
                        "dal tag sintetico '<chatter/>'."
                    ),
                    suggestion="<chatter/>",
                    module_name=mod_name,
                )
            )

    return findings


def analyze_security_csv(file_path: str, content: str) -> list[ReviewFinding]:
    """Analizza il file security/ir.model.access.csv di un modulo Odoo."""
    findings: list[ReviewFinding] = []
    mod_name, _ = extract_module_name_from_path(file_path)

    reader = csv.reader(io.StringIO(content.strip()))
    rows = list(reader)
    if not rows:
        findings.append(
            ReviewFinding(
                rule_id="ODOO-S001",
                severity=Severity.ERROR,
                file_path=file_path,
                line=1,
                message="Il file 'ir.model.access.csv' è vuoto.",
                module_name=mod_name,
            )
        )
        return findings

    header = [col.strip() for col in rows[0]]
    if header != EXPECTED_ACL_HEADERS:
        findings.append(
            ReviewFinding(
                rule_id="ODOO-S001",
                severity=Severity.ERROR,
                file_path=file_path,
                line=1,
                message=(
                    f"Intestazione CSV non valida in 'ir.model.access.csv': {header}. "
                    f"Attesa: {EXPECTED_ACL_HEADERS}"
                ),
                suggestion=",".join(EXPECTED_ACL_HEADERS),
                module_name=mod_name,
            )
        )
        return findings

    for idx, row in enumerate(rows[1:], start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != len(EXPECTED_ACL_HEADERS):
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-S001",
                    severity=Severity.ERROR,
                    file_path=file_path,
                    line=idx,
                    message=f"La riga {idx} ha {len(row)} colonne invece di {len(EXPECTED_ACL_HEADERS)}.",
                    module_name=mod_name,
                )
            )
            continue

        acl_id, _, model_xml_id, group_id, _, perm_write, perm_create, perm_unlink = [
            c.strip() for c in row
        ]
        if not group_id and any(p == "1" for p in (perm_write, perm_create, perm_unlink)):
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-S002",
                    severity=Severity.WARNING,
                    file_path=file_path,
                    line=idx,
                    message=(
                        f"La regola ACL '{acl_id}' per '{model_xml_id}' non specifica 'group_id:id' "
                        "ma concede permessi di scrittura/creazione/cancellazione a TUTTI gli utenti "
                        "(inclusi utenti portale/pubblici). Specifica almeno 'base.group_user'."
                    ),
                    module_name=mod_name,
                )
            )

    return findings
