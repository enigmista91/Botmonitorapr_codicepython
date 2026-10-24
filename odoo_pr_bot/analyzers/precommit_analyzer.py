"""Analizzatore per il file .pre-commit-config.yaml nei repository di moduli Odoo (18.0, 19.0, 20.0)."""

from __future__ import annotations

import re

from odoo_pr_bot.models import ReviewFinding, Severity

RECOMMENDED_OCA_HOOK_KEYWORDS = (
    "ruff",
    "black",
    "flake8",
    "pylint",
    "odoo-pr-bot",
    "Botmonitorapr_codicepython",
    "oca-hooks",
)


def analyze_precommit_config(
    file_path: str,
    content: str,
) -> list[ReviewFinding]:
    """Analizza .pre-commit-config.yaml verificando struttura e hook consigliati OCA."""
    findings: list[ReviewFinding] = []
    stripped = content.strip()

    if not stripped:
        findings.append(
            ReviewFinding(
                rule_id="ODOO-PC001",
                severity=Severity.ERROR,
                file_path=file_path,
                line=1,
                message="Il file '.pre-commit-config.yaml' è vuoto.",
            )
        )
        return findings

    if not re.search(r"^\s*repos\s*:", content, re.MULTILINE):
        findings.append(
            ReviewFinding(
                rule_id="ODOO-PC001",
                severity=Severity.ERROR,
                file_path=file_path,
                line=1,
                message="Il file '.pre-commit-config.yaml' non contiene la chiave radice 'repos:'.",
            )
        )
        return findings

    # Controlla che non vengano usati tag fluttuanti non validi (es. rev: master/main vuoto)
    for idx, line in enumerate(content.splitlines(), start=1):
        rev_match = re.match(r"^\s*rev\s*:\s*(.*)$", line)
        if rev_match:
            rev_val = rev_match.group(1).strip().strip("'\"")
            if not rev_val:
                findings.append(
                    ReviewFinding(
                        rule_id="ODOO-PC001",
                        severity=Severity.ERROR,
                        file_path=file_path,
                        line=idx,
                        message="Attributo 'rev:' vuoto in '.pre-commit-config.yaml'. Specifica un tag o commit SHA.",
                    )
                )

    # Verifica presenza di almeno un linter Python/Odoo consigliato
    content_lower = content.lower()
    if not any(kw.lower() in content_lower for kw in RECOMMENDED_OCA_HOOK_KEYWORDS):
        findings.append(
            ReviewFinding(
                rule_id="ODOO-PC002",
                severity=Severity.INFO,
                file_path=file_path,
                line=1,
                message=(
                    "Il file '.pre-commit-config.yaml' non sembra includere linter Python/Odoo "
                    "(es. 'ruff', 'pylint-odoo' o 'odoo-pr-bot')."
                ),
            )
        )

    return findings
