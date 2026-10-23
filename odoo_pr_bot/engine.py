"""Motore centrale di orchestrazione per l'analisi delle PR di moduli Odoo (18.0, 19.0, 20.0)."""

from __future__ import annotations

from odoo_pr_bot.analyzers.ai_reviewer import run_ai_diff_review
from odoo_pr_bot.analyzers.manifest_analyzer import (
    analyze_manifest,
    extract_module_name_from_path,
)
from odoo_pr_bot.analyzers.python_analyzer import analyze_python_file
from odoo_pr_bot.analyzers.xml_csv_analyzer import (
    analyze_security_csv,
    analyze_xml_file,
)
from odoo_pr_bot.config import BotConfig
from odoo_pr_bot.models import (
    ChangedFile,
    ModuleInfo,
    PRContext,
    ReviewFinding,
    ReviewReport,
    Severity,
)
from odoo_pr_bot.workflow.oca_labels import compute_oca_label_changes


async def evaluate_pull_request(
    pr: PRContext,
    config: BotConfig,
) -> ReviewReport:
    """Esegue la review completa (statica Odoo 18/19/20 + sicurezza ACL + AI + workflow OCA)."""
    report = ReviewReport()
    target_series = config.resolve_series_for_branch(pr.base_branch)
    strict_branch_series = pr.base_branch in config.supported_odoo_series

    # 1. Scansiona tutti i file modificati o aggiunti
    for cf in pr.changed_files:
        if cf.status == "removed":
            continue
        mod_name, mod_root = extract_module_name_from_path(cf.filename)
        if mod_name and mod_name not in report.modules:
            report.modules[mod_name] = ModuleInfo(
                name=mod_name, root_path=mod_root or mod_name
            )

        content = cf.content
        if content is None:
            continue

        # a) Manifest (__manifest__.py / __openerp__.py)
        if cf.filename.endswith(("__manifest__.py", "__openerp__.py")):
            mod_info, m_findings = analyze_manifest(
                file_path=cf.filename,
                content=content,
                target_odoo_series=target_series,
                supported_series=config.supported_odoo_series,
                strict_branch_series=strict_branch_series,
            )
            if mod_name and mod_name in report.modules:
                existing = report.modules[mod_name]
                mod_info.new_models = existing.new_models
                mod_info.has_security_csv = existing.has_security_csv
                mod_info.has_readme = existing.has_readme
            report.modules[mod_info.name] = mod_info
            report.findings.extend(m_findings)

        # b) File Python (.py)
        elif cf.filename.endswith(".py"):
            py_res = analyze_python_file(
                file_path=cf.filename,
                content=content,
                target_odoo_series=target_series,
            )
            report.findings.extend(py_res.findings)
            if mod_name and mod_name in report.modules and py_res.declared_models:
                report.modules[mod_name].new_models.extend(py_res.declared_models)

        # c) Viste / Dati XML (.xml)
        elif cf.filename.endswith(".xml"):
            xml_findings = analyze_xml_file(
                file_path=cf.filename,
                content=content,
                target_odoo_series=target_series,
            )
            report.findings.extend(xml_findings)

        # d) Sicurezza ACL (ir.model.access.csv)
        elif cf.filename.endswith("ir.model.access.csv"):
            if mod_name and mod_name in report.modules:
                report.modules[mod_name].has_security_csv = True
            csv_findings = analyze_security_csv(cf.filename, content)
            report.findings.extend(csv_findings)

        # e) README
        elif cf.filename.lower().endswith(("readme.rst", "readme.md")):
            if mod_name and mod_name in report.modules:
                report.modules[mod_name].has_readme = True

    # 2. Controllo incrociato: nuovi modelli Odoo concreti senza ir.model.access.csv nella PR
    for mod_name, mod_info in report.modules.items():
        if mod_info.new_models and not mod_info.has_security_csv:
            manifest_files = mod_info.manifest_data.get("data", [])
            has_acl_in_manifest = any(
                "ir.model.access.csv" in str(f) for f in manifest_files
            )
            if not has_acl_in_manifest:
                models_str = ", ".join(f"`{m}`" for m in mod_info.new_models)
                report.findings.append(
                    ReviewFinding(
                        rule_id="ODOO-S003",
                        severity=Severity.WARNING,
                        file_path=mod_info.manifest_path or f"{mod_info.root_path}/__manifest__.py",
                        line=1,
                        message=(
                            f"Il modulo '{mod_name}' definisce i modelli concreti {models_str} "
                            "ma non include o aggiorna 'security/ir.model.access.csv'."
                        ),
                        suggestion="Aggiungi 'security/ir.model.access.csv' e dichiaralo in 'data' nel manifest.",
                        module_name=mod_name,
                    )
                )

    # 3. Estrazione maintainer (escludendo l'autore stesso della PR)
    maintainers_set: set[str] = set()
    pr_author_lower = (pr.author or "").lower().lstrip("@")
    for mod_info in report.modules.values():
        for maint in mod_info.maintainers:
            if maint.lower() != pr_author_lower:
                maintainers_set.add(maint)
    report.maintainers_to_mention = sorted(maintainers_set)

    # 4. Calcolo label OCA automatiche
    if config.auto_label:
        to_add, to_remove = compute_oca_label_changes(pr, report, config)
        report.labels_to_add = to_add
        report.labels_to_remove = to_remove

    # 5. Code Review assistita da AI (se abilitata e configurata)
    if config.has_ai_credentials:
        report.ai_summary = await run_ai_diff_review(
            changed_files=pr.changed_files,
            config=config,
            target_series=target_series,
        )

    return report


def format_markdown_report(
    pr: PRContext,
    report: ReviewReport,
    config: BotConfig,
) -> str:
    """Genera il report Markdown completo in stile OCA da pubblicare come commento sulla PR."""
    target_series = config.resolve_series_for_branch(pr.base_branch)
    status_badge = (
        "✅ ** Controlli Statici Superati**"
        if report.passed
        else f"❌ **Rilevati {report.error_count} errori bloccanti**"
    )

    lines: list[str] = [
        f"## 🤖 Odoo PR Bot — Report di Review (Serie target: `{target_series}`)",
        "",
        f"{status_badge} | "
        f"🔴 Errori: **{report.error_count}** | "
        f"🟡 Avvisi: **{report.warning_count}** | "
        f"🔵 Info: **{report.info_count}**",
        "",
    ]

    # Moduli toccati
    if report.modules:
        lines.append("### 📦 Moduli Odoo analizzati")
        lines.append("")
        lines.append("| Modulo | Versione | Licenza | Maintainer dichiarati |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for mod in report.modules.values():
            ver = f"`{mod.version}`" if mod.version else "_N/D_"
            lic = f"`{mod.license}`" if mod.license else "_N/D_"
            maints = (
                ", ".join(f"@{m}" for m in mod.maintainers)
                if mod.maintainers
                else "_Nessuno_"
            )
            lines.append(f"| **{mod.name}** | {ver} | {lic} | {maints} |")
        lines.append("")

    # Menzione Maintainer in stile OCA
    if config.mention_maintainers and report.maintainers_to_mention:
        mentions = ", ".join(f"@{m}" for m in report.maintainers_to_mention)
        lines.append(
            f"🔔 **Notifica Maintainer OCA**: {mentions} — questa PR modifica moduli sotto la vostra manutenzione."
        )
        lines.append("")

    # Dettaglio segnalazioni statiche
    if report.findings:
        lines.append("### 🔍 Risultati Analisi Statica Odoo (18.0 / 19.0 / 20.0)")
        lines.append("")
        lines.append("| Severità | Regola | File : Riga | Descrizione |")
        lines.append("| :---: | :--- | :--- | :--- |")
        icon_map = {
            Severity.ERROR: "🔴 `ERROR`",
            Severity.WARNING: "🟡 `WARN`",
            Severity.INFO: "🔵 `INFO`",
        }
        for f in report.findings:
            loc = f"`{f.file_path}:{f.line}`" if f.line else f"`{f.file_path}`"
            msg = f.message.replace("|", "\\|")
            if f.suggestion:
                sugg = f.suggestion.replace("|", "\\|")
                msg += f" *(Suggerimento: `{sugg}`)*"
            lines.append(f"| {icon_map[f.severity]} | `{f.rule_id}` | {loc} | {msg} |")
        lines.append("")
    else:
        lines.append(
            "✨ *Nessuna violazione rilevata nei manifest, nei modelli Python, nelle viste XML e nei file ACL.*"
        )
        lines.append("")

    # Sezione AI Review
    if report.ai_summary:
        lines.append("### 🧠 Analisi AI del Diff")
        lines.append("")
        lines.append(report.ai_summary)
        lines.append("")

    # Azioni sulle label OCA
    if report.labels_to_add or report.labels_to_remove:
        added = ", ".join(f"`+{lbl}`" for lbl in report.labels_to_add)
        removed = ", ".join(f"`-{lbl}`" for lbl in report.labels_to_remove)
        both = " ".join(x for x in (added, removed) if x)
        lines.append(f"🏷️ **Label OCA aggiornate**: {both}")
        lines.append("")

    main_cmd = config.command_prefixes[0] if config.command_prefixes else "/bot"
    lines.append("---")
    lines.append(
        f"💡 *Comandi rapidi: `{main_cmd} review` | `{main_cmd} bump <patch|minor|major>` | "
        f"`{main_cmd} merge [patch|minor|major|nobump]` | `{main_cmd} help`*"
    )
    return "\n".join(lines)
