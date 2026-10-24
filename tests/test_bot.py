"""Suite di test completa per Odoo PR Bot (versioni supportate: 18.0, 19.0, 20.0)."""

from __future__ import annotations

import hashlib
import hmac
import pytest

from odoo_pr_bot.analyzers.manifest_analyzer import (
    analyze_manifest,
    bump_manifest_version,
)
from odoo_pr_bot.analyzers.precommit_analyzer import analyze_precommit_config
from odoo_pr_bot.analyzers.python_analyzer import analyze_python_file
from odoo_pr_bot.analyzers.xml_csv_analyzer import (
    analyze_security_csv,
    analyze_xml_file,
)
from odoo_pr_bot.cli import run_precommit_hook
from odoo_pr_bot.config import BotConfig
from odoo_pr_bot.engine import evaluate_pull_request, format_markdown_report
from odoo_pr_bot.models import ChangedFile, PRContext, Severity
from odoo_pr_bot.webhook_server import verify_webhook_signature
from odoo_pr_bot.workflow.commands import parse_comment_commands
from odoo_pr_bot.workflow.oca_labels import compute_oca_label_changes


@pytest.mark.parametrize("series", ["18.0", "19.0", "20.0"])
def test_manifest_valid_for_supported_series(series: str) -> None:
    manifest_code = f"""{{
    "name": "Sale Order Extension",
    "version": "{series}.1.0.0",
    "author": "OCA, Enigmista91",
    "license": "LGPL-3",
    "depends": ["sale_management"],
    "maintainers": ["enigmista91", "oca-dev"],
    "summary": "Extend sale order workflow",
    "data": ["security/ir.model.access.csv", "views/sale_order_views.xml"],
}}"""
    info, findings = analyze_manifest(
        "sale_order_ext/__manifest__.py",
        manifest_code,
        target_odoo_series=series,
        strict_branch_series=True,
    )
    assert info.name == "sale_order_ext"
    assert info.version == f"{series}.1.0.0"
    assert info.maintainers == ["enigmista91", "oca-dev"]
    assert len(findings) == 0


def test_manifest_rejects_unsupported_series() -> None:
    manifest_code = """{
    "name": "Legacy Module",
    "version": "16.0.1.0.0",
    "author": "OCA",
    "license": "LGPL-3",
    "depends": ["base"],
    "maintainers": ["enigmista91"],
    "summary": "Old version",
}"""
    _, findings = analyze_manifest(
        "legacy_mod/__manifest__.py",
        manifest_code,
        target_odoo_series="18.0",
    )
    rules = {f.rule_id for f in findings}
    assert "ODOO-M003" in rules
    assert any(f.severity == Severity.ERROR for f in findings)


def test_bump_manifest_version_18_19_20() -> None:
    manifest_code = '{\n    "name": "Test",\n    "version": "19.0.1.2.3",\n}\n'
    c_patch, old_v, new_v = bump_manifest_version(
        manifest_code, "patch", target_odoo_series="19.0"
    )
    assert old_v == "19.0.1.2.3"
    assert new_v == "19.0.1.2.4"
    assert '"19.0.1.2.4"' in c_patch

    _, _, new_minor = bump_manifest_version(
        manifest_code, "minor", target_odoo_series="19.0"
    )
    assert new_minor == "19.0.1.3.0"

    _, _, new_major = bump_manifest_version(
        manifest_code, "major", target_odoo_series="20.0"
    )
    assert new_major == "19.0.2.0.0"


def test_python_analyzer_detects_odoo_18_19_20_issues() -> None:
    bad_code = """
from odoo import api, fields, models

class CustomPartner(models.Model):
    _name = "custom.partner"
    _sql_constraints = [
        ("code_uniq", "unique(code)", "Code must be unique!"),
    ]

    @api.multi
    def name_get(self, extra=[]):
        print("Debugging partner")
        self.env.cr.execute(f"SELECT id FROM res_partner WHERE name = '{self.name}'")
        count = len(self.env["res.partner"].sudo().search([]))
        self.env.cr.commit()
        return []
"""
    res = analyze_python_file(
        "custom_partner/models/custom_partner.py",
        bad_code,
        target_odoo_series="19.0",
    )
    assert res.declared_models == ["custom.partner"]
    rule_ids = {f.rule_id for f in res.findings}
    assert "ODOO-P001" in rule_ids  # @api.multi
    assert "ODOO-P002" in rule_ids  # SQL injection f-string
    assert "ODOO-P003" in rule_ids  # print()
    assert "ODOO-P004" in rule_ids  # _description mancante
    assert "ODOO-P005" in rule_ids  # .sudo() senza commento
    assert "ODOO-P006" in rule_ids  # argomento mutabile extra=[]
    assert "ODOO-P007" in rule_ids  # len(search()) invece di search_count()
    assert "ODOO-P008" in rule_ids  # cr.commit()
    assert "ODOO-P009" in rule_ids  # _sql_constraints deprecato in 18/19/20
    assert "ODOO-P010" in rule_ids  # name_get rimosso in 18/19/20


def test_xml_and_csv_analyzer_18_19_20() -> None:
    xml_code = """<odoo>
    <record id="view_custom_tree" model="ir.ui.view">
        <field name="name">custom.tree</field>
        <field name="model">custom.partner</field>
        <field name="arch" type="xml">
            <tree string="Partners">
                <field name="name" attrs="{'invisible': [('active', '=', False)]}"/>
            </tree>
            <div class="oe_chatter">
                <field name="message_ids"/>
            </div>
        </field>
    </record>
    <record id="action_custom" model="ir.actions.act_window">
        <field name="view_mode">tree,form</field>
    </record>
</odoo>"""
    xml_findings = analyze_xml_file(
        "custom_partner/views/partner_views.xml",
        xml_code,
        target_odoo_series="20.0",
    )
    xml_rules = {f.rule_id for f in xml_findings}
    assert "ODOO-X003" in xml_rules  # attrs= non supportato
    assert "ODOO-X004" in xml_rules  # <tree> sostituito da <list> in 18/19/20
    assert "ODOO-X005" in xml_rules  # view_mode con tree invece di list
    assert "ODOO-X006" in xml_rules  # oe_chatter invece di <chatter/>

    csv_code = (
        "id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n"
        "access_custom_partner,custom.partner,model_custom_partner,,1,1,1,1\n"
    )
    csv_findings = analyze_security_csv(
        "custom_partner/security/ir.model.access.csv", csv_code
    )
    assert any(f.rule_id == "ODOO-S002" for f in csv_findings)


def test_parse_comment_commands() -> None:
    comment = """
Ottimo lavoro sulla PR!
> /bot merge major
/bot review
/ocabot rebase
/ocabot bump minor
/bot merge patch
"""
    cmds = parse_comment_commands(comment)
    assert len(cmds) == 4
    assert (cmds[0].action, cmds[0].arg) == ("review", None)
    assert (cmds[1].action, cmds[1].arg) == ("rebase", None)
    assert (cmds[2].action, cmds[2].arg) == ("bump", "minor")
    assert (cmds[3].action, cmds[3].arg) == ("merge", "patch")


@pytest.mark.asyncio
async def test_evaluate_pr_and_oca_labels() -> None:
    config = BotConfig(
        target_odoo_series="18.0",
        ai_enabled=False,
        required_approvals=1,
        ready_to_merge_min_days=0,
    )
    pr = PRContext(
        repo_full_name="enigmista91/Botmonitorapr_codicepython",
        pr_number=12,
        title="[19.0][ADD] my_new_addon",
        author="contributor1",
        base_branch="19.0",
        approvals_count=1,
        ci_state="success",
        changed_files=[
            ChangedFile(
                filename="my_new_addon/__manifest__.py",
                content="""{
                    "name": "My New Addon",
                    "version": "19.0.1.0.0",
                    "author": "OCA",
                    "license": "LGPL-3",
                    "depends": ["base"],
                    "maintainers": ["enigmista91", "contributor1"],
                    "summary": "Clean addon for Odoo 19.0",
                }""",
            ),
            ChangedFile(
                filename="my_new_addon/models/my_model.py",
                content="""from odoo import fields, models

class MyModel(models.Model):
    _name = "my.model"
    _description = "My Clean Model"

    name = fields.Char(required=True)
""",
            ),
        ],
    )
    report = await evaluate_pull_request(pr, config)
    assert report.passed
    # Segnala warning ODOO-S003 perché MyModel non ha ir.model.access.csv
    assert any(f.rule_id == "ODOO-S003" for f in report.findings)
    # Maintainer mention contiene enigmista91 ma non contributor1 (autore della PR)
    assert report.maintainers_to_mention == ["enigmista91"]
    assert "19.0" in report.labels_to_add
    assert "approved" in report.labels_to_add
    assert "ready to merge" in report.labels_to_add

    md = format_markdown_report(pr, report, config)
    assert "@enigmista91" in md
    assert "19.0" in md


def test_webhook_signature_verification() -> None:
    secret = "my_super_secret"
    body = b'{"action": "opened"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(body, secret, sig) is True
    assert verify_webhook_signature(body, secret, "sha256=invalid") is False


def test_precommit_config_analyzer() -> None:
    bad_yaml = "repos:\n  - repo: https://github.com/example/hook\n    rev:\n"
    findings = analyze_precommit_config(".pre-commit-config.yaml", bad_yaml)
    rules = {f.rule_id for f in findings}
    assert "ODOO-PC001" in rules
    assert "ODOO-PC002" in rules

    good_yaml = (
        "repos:\n"
        "  - repo: https://github.com/enigmista91/Botmonitorapr_codicepython\n"
        "    rev: main\n"
        "    hooks:\n"
        "      - id: odoo-pr-bot\n"
    )
    assert len(analyze_precommit_config(".pre-commit-config.yaml", good_yaml)) == 0


@pytest.mark.asyncio
async def test_precommit_cli_hook(tmp_path) -> None:
    mod_dir = tmp_path / "sale_clean"
    mod_dir.mkdir()
    manifest_file = mod_dir / "__manifest__.py"
    manifest_file.write_text(
        '{\n'
        '    "name": "Sale Clean",\n'
        '    "version": "18.0.1.0.0",\n'
        '    "author": "OCA",\n'
        '    "license": "LGPL-3",\n'
        '    "depends": ["base"],\n'
        '    "maintainers": ["enigmista91"],\n'
        '    "summary": "Clean",\n'
        '}\n',
        encoding="utf-8",
    )
    exit_code = await run_precommit_hook([str(manifest_file)], series="18.0")
    assert exit_code == 0

