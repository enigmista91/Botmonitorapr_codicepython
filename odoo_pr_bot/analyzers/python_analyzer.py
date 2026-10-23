"""Analizzatore AST e statico per file Python di moduli Odoo (18.0, 19.0, 20.0)."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from odoo_pr_bot.analyzers.manifest_analyzer import extract_module_name_from_path
from odoo_pr_bot.models import ReviewFinding, Severity

DEPRECATED_API_DECORATORS = {
    "multi": "@api.multi è stato rimosso. Tutti i metodi lavorano su recordset per default.",
    "one": "@api.one è stato rimosso. Itera esplicitamente con 'for record in self:'.",
    "returns": "@api.returns è deprecato nelle versioni recenti di Odoo (18.0/19.0/20.0).",
    "cr": "@api.cr è stato rimosso.",
    "uid": "@api.uid è stato rimosso.",
    "model_cr": "@api.model_cr è stato rimosso; usa _register_hook().",
}

DEPRECATED_MODEL_METHODS_18_PLUS = {
    "name_get": (
        "ODOO-P010",
        Severity.ERROR,
        "Il metodo 'name_get()' è stato rimosso in Odoo 18.0/19.0/20.0. "
        "Usa '@api.depends(...)' con 'def _compute_display_name(self):' impostando 'record.display_name = ...'.",
    ),
    "fields_view_get": (
        "ODOO-P011",
        Severity.ERROR,
        "Il metodo 'fields_view_get()' è stato rimosso. In Odoo 18.0/19.0/20.0 usa '_get_view()' o 'get_views()'.",
    ),
}

ODOO_MODEL_BASES = {
    "models.Model",
    "models.TransientModel",
    "models.AbstractModel",
    "Model",
    "TransientModel",
    "AbstractModel",
}


@dataclass
class PythonAnalysisResult:
    """Risultato dell'analisi di un singolo file Python Odoo."""

    findings: list[ReviewFinding] = field(default_factory=list)
    declared_models: list[str] = field(default_factory=list)


def analyze_python_file(
    file_path: str,
    content: str,
    target_odoo_series: str = "18.0",
) -> PythonAnalysisResult:
    """Esegue l'analisi AST su un file Python Odoo (models, wizards, controllers)."""
    result = PythonAnalysisResult()
    mod_name, _ = extract_module_name_from_path(file_path)

    # Ignora __manifest__.py che viene gestito da manifest_analyzer
    if file_path.endswith(("__manifest__.py", "__openerp__.py")):
        return result

    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as exc:
        result.findings.append(
            ReviewFinding(
                rule_id="ODOO-P000",
                severity=Severity.ERROR,
                file_path=file_path,
                line=exc.lineno or 1,
                message=f"Errore di sintassi Python: {exc.msg}",
                module_name=mod_name,
            )
        )
        return result

    try:
        major_series = int(target_odoo_series.split(".")[0])
    except ValueError:
        major_series = 18

    lines = content.splitlines()
    visitor = _OdooAstVisitor(
        file_path=file_path,
        module_name=mod_name,
        lines=lines,
        major_series=major_series,
    )
    visitor.visit(tree)

    result.findings.extend(visitor.findings)
    result.declared_models.extend(visitor.declared_models)
    return result


class _OdooAstVisitor(ast.NodeVisitor):
    """Visitatore AST specializzato nelle linee guida OCA e Odoo 18.0 / 19.0 / 20.0."""

    def __init__(
        self,
        file_path: str,
        module_name: str | None,
        lines: list[str],
        major_series: int = 18,
    ) -> None:
        self.file_path = file_path
        self.module_name = module_name
        self.lines = lines
        self.major_series = major_series
        self.findings: list[ReviewFinding] = []
        self.declared_models: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        base_names = [self._expr_to_name(b) for b in node.bases]
        is_odoo_model = any(b in ODOO_MODEL_BASES for b in base_names)
        is_concrete_model = any(
            b in ("models.Model", "models.TransientModel", "Model", "TransientModel")
            for b in base_names
        )

        if is_odoo_model:
            model_name: str | None = None
            has_inherit = False
            has_description = False

            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            if target.id == "_name":
                                val = getattr(stmt.value, "value", getattr(stmt.value, "s", None))
                                if isinstance(val, str):
                                    model_name = val
                            elif target.id == "_inherit":
                                has_inherit = True
                            elif target.id == "_description":
                                has_description = True
                            elif target.id == "_sql_constraints" and self.major_series >= 18:
                                # ODOO-P009: _sql_constraints deprecato in Odoo 19+/20+ (e in transizione da 18+)
                                sev = (
                                    Severity.WARNING
                                    if self.major_series == 18
                                    else Severity.ERROR
                                )
                                self.findings.append(
                                    ReviewFinding(
                                        rule_id="ODOO-P009",
                                        severity=sev,
                                        file_path=self.file_path,
                                        line=stmt.lineno,
                                        message=(
                                            f"In Odoo {self.major_series}.0 '_sql_constraints' è deprecato/sostituito. "
                                            "Usa attributi di classe 'models.Constraint' "
                                            "(es. `_check_qty = models.Constraint('CHECK(qty >= 0)', 'Messaggio')`)."
                                        ),
                                        module_name=self.module_name,
                                    )
                                )

            if model_name and is_concrete_model and not has_inherit:
                self.declared_models.append(model_name)

            # Regola ODOO-P004: Nuovo modello concreto senza _description
            if model_name and is_concrete_model and not has_inherit and not has_description:
                self.findings.append(
                    ReviewFinding(
                        rule_id="ODOO-P004",
                        severity=Severity.WARNING,
                        file_path=self.file_path,
                        line=node.lineno,
                        message=(
                            f"Il modello Odoo '{model_name}' (classe '{node.name}') definisce "
                            f"'_name' ma non specifica '_description'."
                        ),
                        suggestion=f'_description = "{node.name}"',
                        module_name=self.module_name,
                    )
                )

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # 1. Controllo metodi rimossi in Odoo 18.0, 19.0, 20.0 (name_get, fields_view_get)
        if self.major_series >= 18 and node.name in DEPRECATED_MODEL_METHODS_18_PLUS:
            rule_id, sev, msg = DEPRECATED_MODEL_METHODS_18_PLUS[node.name]
            self.findings.append(
                ReviewFinding(
                    rule_id=rule_id,
                    severity=sev,
                    file_path=self.file_path,
                    line=node.lineno,
                    message=msg,
                    module_name=self.module_name,
                )
            )

        # 2. Controllo decoratori deprecati (@api.multi, @api.one, ecc.)
        for dec in node.decorator_list:
            dec_name = self._expr_to_name(dec)
            if dec_name.startswith("api."):
                short = dec_name.split(".", 1)[1]
                if short in DEPRECATED_API_DECORATORS:
                    self.findings.append(
                        ReviewFinding(
                            rule_id="ODOO-P001",
                            severity=Severity.ERROR,
                            file_path=self.file_path,
                            line=dec.lineno,
                            message=DEPRECATED_API_DECORATORS[short],
                            module_name=self.module_name,
                        )
                    )

        # 3. Argomenti di default mutabili (es. def action_foo(self, vals={}))
        for default in node.args.defaults + [
            d for d in node.args.kw_defaults if d is not None
        ]:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.findings.append(
                    ReviewFinding(
                        rule_id="ODOO-P006",
                        severity=Severity.WARNING,
                        file_path=self.file_path,
                        line=node.lineno,
                        message=(
                            f"Argomento di default mutabile nel metodo '{node.name}'. "
                            "Usa 'None' come default e inizializza dentro il metodo."
                        ),
                        module_name=self.module_name,
                    )
                )

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._expr_to_name(node.func)

        # 1. ODOO-P002: SQL Injection in cr.execute / self.env.cr.execute / self._cr.execute
        if func_name.endswith(("cr.execute", "_cr.execute")) and node.args:
            first_arg = node.args[0]
            if self._is_unsafe_sql_expr(first_arg):
                self.findings.append(
                    ReviewFinding(
                        rule_id="ODOO-P002",
                        severity=Severity.ERROR,
                        file_path=self.file_path,
                        line=node.lineno,
                        message=(
                            "Potenziale SQL Injection in 'cr.execute()': evita f-string, "
                            "operatore '%' o '.format()' per costruire query SQL. "
                            "In Odoo 18.0/19.0/20.0 usa 'odoo.tools.SQL(...)' o parametri di query 'cr.execute(query, params)'."
                        ),
                        module_name=self.module_name,
                    )
                )

        # 2. ODOO-P003: print(), breakpoint(), pdb.set_trace()
        if func_name in ("print", "breakpoint", "pdb.set_trace", "ipdb.set_trace"):
            severity = (
                Severity.ERROR
                if func_name in ("breakpoint", "pdb.set_trace", "ipdb.set_trace")
                else Severity.WARNING
            )
            self.findings.append(
                ReviewFinding(
                    rule_id="ODOO-P003",
                    severity=severity,
                    file_path=self.file_path,
                    line=node.lineno,
                    message=(
                        f"Chiamata di debug '{func_name}()' rilevata. "
                        "Usa 'logging.getLogger(__name__)' al posto di print/debugger nei moduli Odoo."
                    ),
                    module_name=self.module_name,
                )
            )

        # 3. ODOO-P005: Uso di .sudo() senza commento esplicativo
        if isinstance(node.func, ast.Attribute) and node.func.attr == "sudo":
            line_text = (
                self.lines[node.lineno - 1]
                if 0 < node.lineno <= len(self.lines)
                else ""
            )
            prev_line = (
                self.lines[node.lineno - 2]
                if 1 < node.lineno <= len(self.lines)
                else ""
            )
            if "#" not in line_text and "#" not in prev_line:
                self.findings.append(
                    ReviewFinding(
                        rule_id="ODOO-P005",
                        severity=Severity.INFO,
                        file_path=self.file_path,
                        line=node.lineno,
                        message=(
                            "Uso di '.sudo()' rilevato senza commento di giustificazione. "
                            "Secondo le linee guida OCA, documenta perché viene bypassata la sicurezza ACL/Record Rule."
                        ),
                        module_name=self.module_name,
                    )
                )

        # 4. ODOO-P007: len(self.search(...)) invece di search_count(...)
        if func_name == "len" and len(node.args) == 1 and isinstance(node.args[0], ast.Call):
            inner_func = node.args[0].func
            if isinstance(inner_func, ast.Attribute) and inner_func.attr == "search":
                self.findings.append(
                    ReviewFinding(
                        rule_id="ODOO-P007",
                        severity=Severity.WARNING,
                        file_path=self.file_path,
                        line=node.lineno,
                        message=(
                            "Uso di 'len(model.search(...))' rilevato. "
                            "Usa 'model.search_count(...)' per evitare di istanziare inutilmente i record in memoria."
                        ),
                        module_name=self.module_name,
                    )
                )

        # 5. ODOO-P008: Chiamata diretta a cr.commit()
        if func_name.endswith(("cr.commit", "_cr.commit")):
            self.findings.append(
                ReviewFinding(
                    rule_id="ODOO-P008",
                    severity=Severity.ERROR,
                    file_path=self.file_path,
                    line=node.lineno,
                    message=(
                        "Chiamata diretta a 'cr.commit()' rilevata! "
                        "Il framework Odoo gestisce autonomamente le transazioni; un commit manuale "
                        "può corrompere l'integrità transazionale e i test."
                    ),
                    module_name=self.module_name,
                )
            )

        self.generic_visit(node)

    @staticmethod
    def _is_unsafe_sql_expr(node: ast.AST) -> bool:
        """Verifica se l'argomento della query è costruito con f-string, % o .format()."""
        if isinstance(node, ast.JoinedStr):  # f-string
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            return True
        return False

    def _expr_to_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = self._expr_to_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Call):
            return self._expr_to_name(node.func)
        return ""
