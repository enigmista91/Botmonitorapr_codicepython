"""Parser ed esecutore di comandi /bot e /ocabot nei commenti delle PR."""

from __future__ import annotations

from dataclasses import dataclass
import re

VALID_BUMP_MODES = ("patch", "minor", "major", "nobump")


@dataclass
class BotCommand:
    """Rappresenta un comando invocato in un commento PR (es. `/bot merge patch`)."""

    prefix: str
    action: str  # review, merge, bump, help
    arg: str | None = None
    raw_line: str = ""


def parse_comment_commands(
    comment_body: str,
    prefixes: list[str] | None = None,
) -> list[BotCommand]:
    """Estrae tutti i comandi destinati al bot presenti nel testo di un commento GitHub.

    Comandi supportati (con `/bot` o `/ocabot`):
    - `/bot review`
    - `/bot merge [patch|minor|major|nobump]`
    - `/bot bump <patch|minor|major>`
    - `/bot help`
    """
    if not comment_body:
        return []
    prefixes = prefixes or ["/bot", "/ocabot"]
    escaped_prefixes = "|".join(re.escape(p) for p in prefixes)
    cmd_re = re.compile(
        rf"^\s*({escaped_prefixes})\s+([a-zA-Z_-]+)(?:\s+([a-zA-Z0-9_.-]+))?\s*$",
        re.IGNORECASE,
    )

    commands: list[BotCommand] = []
    for line in comment_body.splitlines():
        # Ignora righe citate in markdown (> ...)
        if line.lstrip().startswith(">"):
            continue
        match = cmd_re.match(line)
        if not match:
            continue
        prefix = match.group(1).lower()
        action = match.group(2).lower()
        arg = match.group(3).lower() if match.group(3) else None

        if action in ("review", "check"):
            commands.append(
                BotCommand(prefix=prefix, action="review", arg=arg, raw_line=line.strip())
            )
        elif action == "merge":
            bump_mode = arg if arg in VALID_BUMP_MODES else "nobump"
            commands.append(
                BotCommand(
                    prefix=prefix, action="merge", arg=bump_mode, raw_line=line.strip()
                )
            )
        elif action == "bump":
            bump_mode = arg if arg in ("patch", "minor", "major") else "patch"
            commands.append(
                BotCommand(
                    prefix=prefix, action="bump", arg=bump_mode, raw_line=line.strip()
                )
            )
        elif action == "help":
            commands.append(
                BotCommand(prefix=prefix, action="help", arg=None, raw_line=line.strip())
            )

    return commands


def format_help_message(prefixes: list[str] | None = None) -> str:
    """Restituisce il messaggio di aiuto con l'elenco dei comandi supportati dal bot."""
    prefixes = prefixes or ["/bot", "/ocabot"]
    main_prefix = prefixes[0]
    aliases = ", ".join(f"`{p}`" for p in prefixes)
    return (
        "### 🤖 Comandi Odoo PR Bot disponibili\n\n"
        f"Puoi invocare il bot nei commenti della PR usando {aliases} (versioni Odoo supportate: **18.0**, **19.0**, **20.0**):\n\n"
        "| Comando | Descrizione |\n"
        "| :--- | :--- |\n"
        f"| `{main_prefix} review` | Esegue nuovamente l'analisi statica Odoo (18.0/19.0/20.0), il controllo ACL e la review AI |\n"
        f"| `{main_prefix} bump patch` | Incrementa la versione patch (`X.Y.Z+1`) nei `__manifest__.py` dei moduli toccati |\n"
        f"| `{main_prefix} bump minor` | Incrementa la versione minor (`X.Y+1.0`) nei `__manifest__.py` dei moduli toccati |\n"
        f"| `{main_prefix} bump major` | Incrementa la versione major (`X+1.0.0`) nei `__manifest__.py` dei moduli toccati |\n"
        f"| `{main_prefix} merge [patch\\|minor\\|major\\|nobump]` | Verifica CI e approvazioni, aggiorna opzionalmente la versione in `__manifest__.py` ed esegue il merge della PR |\n"
        f"| `{main_prefix} help` | Mostra questa guida |\n"
    )
