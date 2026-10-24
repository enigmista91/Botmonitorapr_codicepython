"""Interfaccia a riga di comando (CLI) per scansione locale, pre-commit, GitHub Actions e Webhook Server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

import uvicorn

from odoo_pr_bot.config import BotConfig
from odoo_pr_bot.engine import evaluate_pull_request, format_markdown_report
from odoo_pr_bot.models import ChangedFile, PRContext, Severity
from odoo_pr_bot.webhook_server import (
    handle_comment_commands,
    handle_pr_review_flow,
)
from odoo_pr_bot.workflow.commands import parse_comment_commands


def collect_local_files(target_dir: Path) -> list[ChangedFile]:
    """Raccoglie i file di uno o più moduli Odoo presenti in una cartella locale."""
    changed_files: list[ChangedFile] = []
    valid_exts = (".py", ".xml", ".csv", ".rst", ".md", ".yaml", ".yml")

    for path in sorted(target_dir.rglob("*")):
        if not path.is_file():
            continue
        if any(
            part in ("__pycache__", "venv", ".venv", ".git", ".pytest_cache")
            for part in path.parts
        ):
            continue
        if path.suffix.lower() not in valid_exts:
            continue

        rel_path = path.relative_to(target_dir).as_posix()
        if (target_dir / "__manifest__.py").exists():
            rel_path = f"{target_dir.name}/{rel_path}"

        content = path.read_text(encoding="utf-8", errors="replace")
        changed_files.append(
            ChangedFile(
                filename=rel_path,
                status="modified",
                patch="",
                content=content,
            )
        )
    return changed_files


async def run_local_scan(path_str: str, series: str) -> int:
    """Esegue la review statica su una directory locale e stampa il report Markdown."""
    target_dir = Path(path_str).resolve()
    if not target_dir.exists():
        print(f"Errore: percorso '{target_dir}' non trovato.", file=sys.stderr)
        return 2

    config = BotConfig(target_odoo_series=series, ai_enabled=False)
    files = collect_local_files(target_dir)
    pr_ctx = PRContext(
        repo_full_name="local/addons",
        pr_number=0,
        title=f"Local scan ({target_dir.name})",
        base_branch=series,
        changed_files=files,
    )
    report = await evaluate_pull_request(pr_ctx, config)
    md = format_markdown_report(pr_ctx, report, config)
    print(md)
    return 0 if report.passed else 1


async def run_precommit_hook(
    filenames: list[str],
    series: str,
    fail_on_warning: bool = False,
) -> int:
    """Esegue i controlli rapidi come hook pre-commit sui file passati in argomento."""
    changed_files: list[ChangedFile] = []
    seen_paths: set[str] = set()
    cwd = Path.cwd().resolve()

    for raw_file in filenames:
        p = Path(raw_file).resolve()
        if not p.is_file():
            continue
        try:
            rel_path = p.relative_to(cwd).as_posix()
        except ValueError:
            rel_path = p.as_posix()

        if rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)

        content = p.read_text(encoding="utf-8", errors="replace")
        changed_files.append(
            ChangedFile(filename=rel_path, status="modified", content=content)
        )

        # Se il file appartiene a un modulo Odoo, carica anche il suo __manifest__.py e ir.model.access.csv
        # se esistono su disco, così da evitare falsi positivi sul controllo incrociato ODOO-S003.
        parts = rel_path.split("/")
        mod_root_rel = None
        if parts[0] in ("addons", "custom_addons") and len(parts) >= 2:
            mod_root_rel = f"{parts[0]}/{parts[1]}"
        elif len(parts) >= 2 and not parts[0].startswith("."):
            mod_root_rel = parts[0]

        if mod_root_rel:
            for extra_sub in ("__manifest__.py", "security/ir.model.access.csv"):
                extra_rel = f"{mod_root_rel}/{extra_sub}"
                extra_abs = cwd / extra_rel
                if extra_rel not in seen_paths and extra_abs.is_file():
                    seen_paths.add(extra_rel)
                    changed_files.append(
                        ChangedFile(
                            filename=extra_rel,
                            status="modified",
                            content=extra_abs.read_text(
                                encoding="utf-8", errors="replace"
                            ),
                        )
                    )

    if not changed_files:
        return 0

    config = BotConfig(target_odoo_series=series, ai_enabled=False)
    pr_ctx = PRContext(
        repo_full_name="local/pre-commit",
        pr_number=0,
        title="pre-commit check",
        base_branch=series,
        changed_files=changed_files,
    )
    report = await evaluate_pull_request(pr_ctx, config)

    if not report.findings:
        print(f"[odoo-pr-bot] OK ({series}): nessuna violazione rilevata.")
        return 0

    for f in report.findings:
        loc = f"{f.file_path}:{f.line}" if f.line else f.file_path
        sev_label = f.severity.value.upper()
        print(f"[{sev_label}] {f.rule_id} {loc} -> {f.message}")
        if f.suggestion:
            print(f"    Suggerimento: {f.suggestion}")

    if report.error_count > 0:
        print(
            f"\n[odoo-pr-bot] Commit bloccato: {report.error_count} errori, {report.warning_count} avvisi.",
            file=sys.stderr,
        )
        return 1

    if fail_on_warning and report.warning_count > 0:
        print(
            f"\n[odoo-pr-bot] Commit bloccato (--fail-on-warning): {report.warning_count} avvisi.",
            file=sys.stderr,
        )
        return 1

    return 0


async def run_github_action_mode() -> int:
    """Modalità automatica per GitHub Actions (legge GITHUB_EVENT_NAME e GITHUB_EVENT_PATH)."""
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    event_path = os.getenv("GITHUB_EVENT_PATH", "")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    config = BotConfig()

    if not event_path or not Path(event_path).exists():
        print("GITHUB_EVENT_PATH non trovato; eseguo scansione locale della workspace.")
        return await run_local_scan(".", config.target_odoo_series)

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))

    if event_name in ("pull_request", "pull_request_target"):
        pr_number = payload["pull_request"]["number"]
        await handle_pr_review_flow(repo, pr_number, config, post_full_review=True)
        return 0

    if event_name == "issue_comment":
        issue = payload.get("issue", {})
        if "pull_request" in issue:
            comment_body = (payload.get("comment") or {}).get("body", "")
            commands = parse_comment_commands(comment_body, config.command_prefixes)
            if commands:
                await handle_comment_commands(
                    repo, issue["number"], commands, config
                )
        return 0

    return await run_local_scan(".", config.target_odoo_series)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="odoo-pr-bot",
        description="Bot di Review & Automazione PR per moduli Odoo (18.0, 19.0, 20.0)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. scan locale
    scan_p = sub.add_parser(
        "scan", help="Analizza staticamente una cartella di moduli Odoo in locale"
    )
    scan_p.add_argument("path", default=".", nargs="?", help="Percorso degli addon Odoo")
    scan_p.add_argument(
        "--series",
        default=os.getenv("TARGET_ODOO_SERIES", "18.0"),
        choices=["18.0", "19.0", "20.0"],
        help="Serie Odoo di riferimento (18.0, 19.0, 20.0)",
    )

    # 2. pre-commit hook
    pc_p = sub.add_parser(
        "pre-commit",
        help="Esegue il controllo veloce sui file staged passato da pre-commit",
    )
    pc_p.add_argument("filenames", nargs="*", help="Elenco file passati da pre-commit")
    pc_p.add_argument(
        "--series",
        default=os.getenv("TARGET_ODOO_SERIES", "18.0"),
        choices=["18.0", "19.0", "20.0"],
        help="Serie Odoo di riferimento (18.0, 19.0, 20.0)",
    )
    pc_p.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Fallisce anche in presenza di warning (non solo error)",
    )

    # 3. review-pr remota
    rev_p = sub.add_parser(
        "review-pr", help="Esegue la review e aggiorna le label di una PR su GitHub"
    )
    rev_p.add_argument("--repo", required=True, help="Repository nel formato owner/repo")
    rev_p.add_argument("--pr", required=True, type=int, help="Numero della Pull Request")

    # 4. github-action
    sub.add_parser(
        "github-action",
        help="Esegue il bot leggendo automaticamente il contesto GitHub Actions",
    )

    # 5. serve (Webhook Server FastAPI)
    srv_p = sub.add_parser("serve", help="Avvia il server Webhook FastAPI")
    srv_p.add_argument("--host", default="0.0.0.0", help="Host di ascolto")
    srv_p.add_argument(
        "--port", type=int, default=int(os.getenv("PORT", "8000")), help="Porta HTTP"
    )

    args = parser.parse_args(argv)

    if args.command == "scan":
        return asyncio.run(run_local_scan(args.path, args.series))

    if args.command == "pre-commit":
        return asyncio.run(
            run_precommit_hook(args.filenames, args.series, args.fail_on_warning)
        )

    if args.command == "review-pr":
        config = BotConfig()
        asyncio.run(
            handle_pr_review_flow(args.repo, args.pr, config, post_full_review=True)
        )
        return 0

    if args.command == "github-action":
        return asyncio.run(run_github_action_mode())

    if args.command == "serve":
        uvicorn.run("odoo_pr_bot.webhook_server:app", host=args.host, port=args.port)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
