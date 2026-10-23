"""Analizzatore di file __manifest__.py (Odoo 18.0, 19.0, 20.0) e gestione bump versioni."""

from __future__ import annotations

import ast
import re
from typing import Any

from odoo_pr_bot.config import DEFAULT_SUPPORTED_SERIES
from odoo_pr_bot.models import ModuleInfo, ReviewFinding, Severity

REQUIRED_MANIFEST_KEYS = ("name", "version", "depends", "license", "author")
RECOMMENDED_MANIFEST_KEYS = ("website", "category", "summary", "maintainers")

VALID_LICENSES = {
    "LGPL-3",
    "LGPL-3.0-or-later",
    "AGPL-3",
    "AGPL-3.0-or-later",
    "GPL-3",
    "OPL-1",
    "OEEL-1",
    "MIT",
    "BSD-3-Clause",
    "Apache-2.0",
    "Other OSI approved licence",
}

DEPRECATED_MANIFEST_KEYS = {
    "init_xml": "Usa 'data' invece di 'init_xml'.",
    "update_xml": "Usa 'data' invece di 'update_xml'.",
    "demo_xml": "Usa 'demo' invece di 'demo_xml'.",
    "active": "La chiave 'active' è deprecata; usa 'auto_install' se necessario.",
}

# Formato versione OCA / Odoo standard: es. 18.0.1.0.0, 19.0.1.0.0, 20.0.1.0.0
ODOO_VERSION_RE = re.compile(r"^(\d+\.\d+)\.(\d+)\.(\d+)\.(\d+)$")


def extract_module_name_from_path(file_path: str) -> tuple[str | None, str | None]:
    """Estrae il nome del modulo Odoo e il suo root path dal percorso del file."""
    normalized = file_path.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if not parts:
        return None, None

    if parts[0] in ("addons", "odoo/addons", "custom_addons") and len(parts) >= 2:
        return parts[1], f"{parts[0]}/{parts[1]}"
    if parts[0].startswith("."):
        return None, None
    return parts[0], parts[0]


def analyze_manifest(
    file_path: str,
    content: str,
    target_odoo_series: str = "18.0",
    supported_series: tuple[str, ...] = DEFAULT_SUPPORTED_SERIES,
    strict_branch_series: bool = False,
) -> tuple[ModuleInfo, list[ReviewFinding]]:
    """Analizza il contenuto di __manifest__.py per Odoo 18.0, 19.0 e 20.0."""
    mod_name, mod_root = extract_module_name_from_path(file_path)
    mod_name = mod_name or "unknown_module"
    mod_root = mod_root or ""

    module_info = ModuleInfo(
        name=mod_name,
        root_path=mod_root,
        manifest_path=file_path,
    )
    findings: list[ReviewFinding] = []

    if file_path.endswith("__openerp__.py"):
        findings.append(
            ReviewFinding(
                rule_id="ODOO-M000",
                severity=Severity.ERROR,
                file_path=file_path,
                line=1,
                message="Il file '__openerp__.py' è deprecato. Rinominalo in '__manifest__.py'.",
                module_name=mod_name,
            )
        )

    try:
        parsed_ast = ast.parse(content, filename=file_path)
        manifest_dict: dict[str, Any] | None = None
        for node in parsed_ast.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Dict):
                manifest_dict = ast.literal_eval(node.value)
                break
        if manifest_dict is None:
            manifest_dict = ast.literal_eval(content)
        if not isinstance(manifest_dict, dict):
            raise ValueError("Il manifest non contiene un dizionario Python valido.")
    except Exception as exc:
        findings.append(
            ReviewFinding(
                rule_id="ODOO-M001",
                severity=Severity.ERROR,
                file_path=file_path,
                line=1,
                message=f"Impossibile fare il parsing di __manifest__.py come dizionario Python statico: {exc}",
                module_name=mod_name,
            )
        )
        return module_info, findings

    module_info.manifest_data = manifest_dict
    module_info.version = str(manifest_dict.get("version", "")) or None
    module_info.license = str(manifest_dict.get("license", "")) or None
    raw_maintainers = manifest_dict.get("maintainers", [])
    if isinstance(raw_maintainers, (list, tuple)):
        module_info.maintainers = [
            str(m).strip().lstrip("@") for m in raw_maintainers if str(m).strip()
        ]
    elif isinstance(raw_maintainers, str) and raw_maintainers.strip():
        module_info.maintainers = [raw_maintainers.strip().lstrip("@")]

    raw_depends = manifest_dict.get("depends", [])
    if isinstance(raw_depends, (list, tuple)):
        module_info.depends = [str(d) for d in raw_depends]

    # 1. Chiavi obbligatorie
    for req_key in REQUIRED_MANIFEST_KEYS:
        if req_key not in manifest_dict or not manifest_dict.get(req_key):
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-M002",
                    severity=Severity.ERROR,
                    file_path=file_path,
                    line=1,
                    message=f"Chiave obbligatoria '{req_key}' mancante o vuota in __manifest__.py.",
                    suggestion=f"Aggiungi '{req_key}' nel dizionario del manifest.",
                    module_name=mod_name,
                )
            )

    # 2. Formato versione (5 componenti OCA per Odoo 18.0, 19.0, 20.0)
    version = module_info.version
    if version:
        match = ODOO_VERSION_RE.match(version)
        if not match:
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-M003",
                    severity=Severity.WARNING,
                    file_path=file_path,
                    line=_find_key_line(content, "version"),
                    message=(
                        f"Versione '{version}' non conforme allo standard OCA a 5 parti "
                        f"('{target_odoo_series}.X.Y.Z', es. '{target_odoo_series}.1.0.0')."
                    ),
                    suggestion=f'"version": "{target_odoo_series}.1.0.0",',
                    module_name=mod_name,
                )
            )
        else:
            manifest_series = match.group(1)
            if manifest_series not in supported_series:
                findings.append(
                    ReviewFinding(
                        rule_id="ODOO-M003",
                        severity=Severity.ERROR,
                        file_path=file_path,
                        line=_find_key_line(content, "version"),
                        message=(
                            f"La serie Odoo '{manifest_series}' in __manifest__.py non rientra tra le "
                            f"versioni supportate ({', '.join(supported_series)})."
                        ),
                        suggestion=f'"version": "{target_odoo_series}.1.0.0",',
                        module_name=mod_name,
                    )
                )
            elif strict_branch_series and manifest_series != target_odoo_series:
                findings.append(
                    ReviewFinding(
                        rule_id="ODOO-M003",
                        severity=Severity.WARNING,
                        file_path=file_path,
                        line=_find_key_line(content, "version"),
                        message=(
                            f"La serie Odoo nella versione ('{manifest_series}') non corrisponde "
                            f"al branch di destinazione della PR ('{target_odoo_series}')."
                        ),
                        suggestion=f'"version": "{target_odoo_series}.1.0.0",',
                        module_name=mod_name,
                    )
                )

    # 3. Validazione licenza
    lic = module_info.license
    if lic and lic not in VALID_LICENSES:
        findings.append(
            ReviewFinding(
                rule_id="ODOO-M004",
                severity=Severity.WARNING,
                file_path=file_path,
                line=_find_key_line(content, "license"),
                message=f"Licenza '{lic}' non standard. Licenze consigliate OCA/Odoo: LGPL-3, AGPL-3, OPL-1.",
                module_name=mod_name,
            )
        )

    # 4. Chiavi deprecate
    for dep_key, reason in DEPRECATED_MANIFEST_KEYS.items():
        if dep_key in manifest_dict:
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-M005",
                    severity=Severity.ERROR,
                    file_path=file_path,
                    line=_find_key_line(content, dep_key),
                    message=f"Chiave deprecata '{dep_key}' in __manifest__.py. {reason}",
                    module_name=mod_name,
                )
            )

    # 5. Chiavi raccomandate OCA (maintainers, summary)
    for rec_key in ("maintainers", "summary"):
        if rec_key not in manifest_dict:
            findings.append(
                ReviewFinding(
                    rule_id="ODOO-M006",
                    severity=Severity.INFO,
                    file_path=file_path,
                    line=1,
                    message=f"Chiave raccomandata OCA '{rec_key}' non presente in __manifest__.py.",
                    module_name=mod_name,
                )
            )

    return module_info, findings


def bump_manifest_version(
    content: str,
    bump_type: str = "patch",
    target_odoo_series: str = "18.0",
) -> tuple[str, str, str]:
    """Incrementa la versione in __manifest__.py (patch, minor, major).

    Restituisce (nuovo_contenuto, vecchia_versione, nuova_versione).
    """
    bump_type = bump_type.lower().strip()
    if bump_type not in ("patch", "minor", "major"):
        raise ValueError(f"Tipo di bump non valido: {bump_type}. Usa patch, minor o major.")

    pattern = re.compile(
        r"""(['"]version['"]\s*:\s*['"])([^'"]+)(['"])""",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        raise ValueError("Chiave 'version' non trovata nel file __manifest__.py.")

    old_version = match.group(2).strip()
    ver_match = ODOO_VERSION_RE.match(old_version)
    if ver_match:
        series = ver_match.group(1)
        major = int(ver_match.group(2))
        minor = int(ver_match.group(3))
        patch = int(ver_match.group(4))
    else:
        parts = [int(p) for p in re.findall(r"\d+", old_version)]
        series = target_odoo_series
        if len(parts) == 3:
            major, minor, patch = parts[0], parts[1], parts[2]
        elif len(parts) >= 5:
            series = f"{parts[0]}.{parts[1]}"
            major, minor, patch = parts[2], parts[3], parts[4]
        else:
            major, minor, patch = 1, 0, 0

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1

    new_version = f"{series}.{major}.{minor}.{patch}"
    new_content = (
        content[: match.start()]
        + f"{match.group(1)}{new_version}{match.group(3)}"
        + content[match.end() :]
    )
    return new_content, old_version, new_version


def _find_key_line(content: str, key: str) -> int:
    """Trova il numero di riga approssimativo di una chiave nel manifest."""
    key_re = re.compile(rf"""['"]{re.escape(key)}['"]\s*:""")
    for idx, line in enumerate(content.splitlines(), start=1):
        if key_re.search(line):
            return idx
    return 1
