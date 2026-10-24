"""Modelli dati per i risultati di analisi, moduli Odoo e contesto PR."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Livello di severità di una segnalazione durante la review."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ReviewFinding:
    """Singola segnalazione rilevata dall'analisi statica o AI."""

    rule_id: str
    severity: Severity
    file_path: str
    message: str
    line: int | None = None
    suggestion: str | None = None
    module_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "file_path": self.file_path,
            "line": self.line,
            "message": self.message,
            "suggestion": self.suggestion,
            "module_name": self.module_name,
        }


@dataclass
class ModuleInfo:
    """Metadati estratti da un modulo Odoo toccato nella PR."""

    name: str
    root_path: str
    manifest_path: str | None = None
    manifest_data: dict[str, Any] = field(default_factory=dict)
    version: str | None = None
    license: str | None = None
    maintainers: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    new_models: list[str] = field(default_factory=list)
    has_security_csv: bool = False
    has_readme: bool = False


@dataclass
class ChangedFile:
    """File modificato all'interno di una Pull Request o directory."""

    filename: str
    status: str = "modified"  # added, modified, removed, renamed
    patch: str = ""
    content: str | None = None
    additions: int = 0
    deletions: int = 0


@dataclass
class PRContext:
    """Contesto completo di una Pull Request GitHub."""

    repo_full_name: str
    pr_number: int
    title: str
    body: str = ""
    author: str = ""
    base_branch: str = "18.0"
    head_branch: str = ""
    head_sha: str = ""
    head_repo_full_name: str = ""
    is_draft: bool = False
    created_at_iso: str | None = None
    current_labels: list[str] = field(default_factory=list)
    approvals_count: int = 0
    changes_requested_count: int = 0
    ci_state: str = "pending"  # success, failure, pending
    changed_files: list[ChangedFile] = field(default_factory=list)


@dataclass
class ReviewReport:
    """Risultato complessivo della review su una PR di moduli Odoo."""

    findings: list[ReviewFinding] = field(default_factory=list)
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    maintainers_to_mention: list[str] = field(default_factory=list)
    labels_to_add: list[str] = field(default_factory=list)
    labels_to_remove: list[str] = field(default_factory=list)
    ai_summary: str | None = None

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.INFO)

    @property
    def passed(self) -> bool:
        return self.error_count == 0
