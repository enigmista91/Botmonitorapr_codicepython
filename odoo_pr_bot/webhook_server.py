"""Server Webhook FastAPI per ricevere gli eventi GitHub in stile OCA/oca-github-bot."""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from odoo_pr_bot.config import BotConfig
from odoo_pr_bot.engine import evaluate_pull_request, format_markdown_report
from odoo_pr_bot.github_client import GitHubClient
from odoo_pr_bot.workflow.commands import (
    BotCommand,
    format_help_message,
    parse_comment_commands,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Odoo PR Review & OCA Workflow Bot",
    description="Bot per review statica/AI e automazione PR di moduli Odoo (18.0, 19.0, 20.0)",
    version="1.0.0",
)


def verify_webhook_signature(
    payload_body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    """Verifica la firma HMAC SHA-256 inviata da GitHub in X-Hub-Signature-256."""
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = (
        "sha256="
        + hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Endpoint di stato del server e delle versioni Odoo supportate."""
    cfg = BotConfig()
    return {
        "status": "ok",
        "bot": "odoo-pr-bot",
        "supported_odoo_series": list(cfg.supported_odoo_series),
        "default_target_series": cfg.target_odoo_series,
        "command_prefixes": cfg.command_prefixes,
    }


@app.post("/webhook")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: Optional[str] = Header(default=None),
    x_hub_signature_256: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """Riceve e smista i webhook GitHub (pull_request, pull_request_review, issue_comment)."""
    config = BotConfig()
    raw_body = await request.body()

    if not verify_webhook_signature(
        raw_body, config.github_webhook_secret, x_hub_signature_256
    ):
        raise HTTPException(status_code=401, detail="Firma webhook non valida.")

    payload = await request.json()
    event = x_github_event or "unknown"

    if event == "ping":
        return {"status": "pong", "supported_series": list(config.supported_odoo_series)}

    if event == "pull_request":
        action = payload.get("action", "")
        if action in (
            "opened",
            "synchronize",
            "reopened",
            "ready_for_review",
            "converted_to_draft",
            "edited",
        ):
            repo_name = payload["repository"]["full_name"]
            pr_number = payload["pull_request"]["number"]
            post_full_review = action in ("opened", "synchronize", "ready_for_review")
            background_tasks.add_task(
                handle_pr_review_flow,
                repo_name,
                pr_number,
                config,
                post_full_review,
            )
            return {
                "status": "queued",
                "event": event,
                "action": action,
                "pr": pr_number,
            }

    elif event == "pull_request_review":
        repo_name = payload["repository"]["full_name"]
        pr_number = payload["pull_request"]["number"]
        background_tasks.add_task(
            handle_pr_review_flow,
            repo_name,
            pr_number,
            config,
            False,
        )
        return {"status": "queued", "event": event, "pr": pr_number}

    elif event == "issue_comment":
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        if action == "created" and "pull_request" in issue:
            comment_body = (payload.get("comment") or {}).get("body", "")
            commands = parse_comment_commands(comment_body, config.command_prefixes)
            if commands:
                repo_name = payload["repository"]["full_name"]
                pr_number = issue["number"]
                background_tasks.add_task(
                    handle_comment_commands,
                    repo_name,
                    pr_number,
                    commands,
                    config,
                )
                return {
                    "status": "queued_commands",
                    "pr": pr_number,
                    "commands": [c.action for c in commands],
                }

    return {"status": "ignored", "event": event}


async def handle_pr_review_flow(
    repo_full_name: str,
    pr_number: int,
    config: BotConfig,
    post_full_review: bool = True,
) -> None:
    """Esegue il ciclo completo di review e aggiornamento label su una PR GitHub."""
    gh = GitHubClient(config)
    pr = await gh.build_pr_context(repo_full_name, pr_number)
    report = await evaluate_pull_request(pr, config)

    if config.auto_label and (report.labels_to_add or report.labels_to_remove):
        await gh.apply_label_changes(
            repo_full_name,
            pr_number,
            report.labels_to_add,
            report.labels_to_remove,
        )

    if post_full_review:
        md_summary = format_markdown_report(pr, report, config)
        await gh.post_inline_review(pr, report.findings, md_summary)


async def handle_comment_commands(
    repo_full_name: str,
    pr_number: int,
    commands: list[BotCommand],
    config: BotConfig,
) -> None:
    """Esegue i comandi `/bot` o `/ocabot` invocati nei commenti della PR."""
    gh = GitHubClient(config)

    for cmd in commands:
        if cmd.action == "help":
            await gh.post_issue_comment(
                repo_full_name,
                pr_number,
                format_help_message(config.command_prefixes),
            )

        elif cmd.action == "review":
            await handle_pr_review_flow(
                repo_full_name, pr_number, config, post_full_review=True
            )

        elif cmd.action == "bump":
            pr = await gh.build_pr_context(repo_full_name, pr_number)
            bump_type = cmd.arg or "patch"
            bumped = await gh.bump_pr_manifests(pr, bump_type)
            if bumped:
                details = "\n".join(
                    f"- `{path}`: `{old_v}` ➡️ **`{new_v}`**"
                    for path, old_v, new_v in bumped
                )
                await gh.post_issue_comment(
                    repo_full_name,
                    pr_number,
                    f"🚀 **Version Bump (`{bump_type}`) completato:**\n\n{details}",
                )
            else:
                await gh.post_issue_comment(
                    repo_full_name,
                    pr_number,
                    "⚠️ Nessun file `__manifest__.py` idoneo trovato per il bump di versione.",
                )

        elif cmd.action == "merge":
            pr = await gh.build_pr_context(repo_full_name, pr_number)
            report = await evaluate_pull_request(pr, config)

            if not report.passed:
                await gh.post_issue_comment(
                    repo_full_name,
                    pr_number,
                    f"⛔ **Merge bloccato**: sono presenti **{report.error_count}** errori statici nella PR. "
                    "Risolvi gli errori prima di eseguire il merge.",
                )
                continue

            if pr.ci_state == "failure":
                await gh.post_issue_comment(
                    repo_full_name,
                    pr_number,
                    "⛔ **Merge bloccato**: i controlli CI della PR sono in stato `failure`.",
                )
                continue

            # Se è richiesto un bump di versione prima del merge (/bot merge patch|minor|major)
            bump_notes = ""
            if cmd.arg in ("patch", "minor", "major"):
                bumped = await gh.bump_pr_manifests(pr, cmd.arg)
                if bumped:
                    bump_notes = (
                        "\n\nVersioni aggiornate prima del merge:\n"
                        + "\n".join(
                            f"- `{p}`: `{ov}` ➡️ **`{nv}`**" for p, ov, nv in bumped
                        )
                    )

            merged_ok, msg = await gh.merge_pull_request(pr)
            if merged_ok:
                await gh.post_issue_comment(
                    repo_full_name,
                    pr_number,
                    f"✅ **PR unita con successo!** Grazie @{pr.author} per il contributo.{bump_notes}",
                )
            else:
                await gh.post_issue_comment(
                    repo_full_name,
                    pr_number,
                    f"❌ **Impossibile completare il merge automatico**: {msg}",
                )
