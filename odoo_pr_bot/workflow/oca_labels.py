"""Calcolo automatico delle label PR secondo il workflow OCA."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from odoo_pr_bot.config import BotConfig
from odoo_pr_bot.models import PRContext, ReviewReport

WIP_PATTERN = re.compile(r"^\s*(\[?\s*wip\s*\]?|wip\s*:|draft\s*:)", re.IGNORECASE)


def is_pr_wip(pr: PRContext) -> bool:
    """Rileva se la PR è marcata come Work In Progress (nel titolo o come Draft GitHub)."""
    if pr.is_draft:
        return True
    return bool(WIP_PATTERN.search(pr.title or ""))


def compute_oca_label_changes(
    pr: PRContext,
    report: ReviewReport,
    config: BotConfig,
) -> tuple[list[str], list[str]]:
    """Determina quali label aggiungere e rimuovere seguendo lo standard OCA/oca-github-bot.

    Label gestite:
    - `wip`: se la PR è draft o inizia con `[WIP]` / `wip:`
    - `needs review`: quando non è WIP, non ci sono errori bloccanti e la CI non è fallita
    - `approved`: quando il numero di approvazioni >= `config.required_approvals`
    - `changes requested`: quando ci sono richieste di modifica attive o errori statici
    - `ready to merge`: quando è approvata, CI verde, non WIP e ha l'età minima configurata
    - `<serie>` (`18.0`, `19.0`, `20.0`): etichetta della versione Odoo target
    """
    desired: set[str] = set()
    undesired: set[str] = set()

    target_series = config.resolve_series_for_branch(pr.base_branch)
    if target_series in config.supported_odoo_series:
        desired.add(target_series)

    wip = is_pr_wip(pr)
    has_blocking_issues = (
        report.error_count > 0
        or pr.changes_requested_count > 0
        or pr.ci_state == "failure"
    )

    if wip:
        desired.add("wip")
        undesired.update({"needs review", "ready to merge"})
    else:
        undesired.add("wip")
        if has_blocking_issues:
            desired.add("changes requested")
            undesired.update({"needs review", "ready to merge"})
        else:
            undesired.add("changes requested")
            if pr.approvals_count >= config.required_approvals:
                desired.add("approved")
                undesired.add("needs review")
                if pr.ci_state == "success" and _is_old_enough(
                    pr.created_at_iso, config.ready_to_merge_min_days
                ):
                    desired.add("ready to merge")
            else:
                desired.add("needs review")
                undesired.update({"approved", "ready to merge"})

    current = set(pr.current_labels)
    to_add = sorted(desired - current)
    to_remove = sorted(undesired & current)
    return to_add, to_remove


def _is_old_enough(created_at_iso: str | None, min_days: int) -> bool:
    """Verifica se la PR è aperta da almeno `min_days` giorni."""
    if min_days <= 0 or not created_at_iso:
        return True
    try:
        dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).days >= min_days
    except ValueError:
        return True
