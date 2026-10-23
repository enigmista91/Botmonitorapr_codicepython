"""Configurazione centrale del bot tramite variabili d'ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_SUPPORTED_SERIES = ("18.0", "19.0", "20.0")


@dataclass
class BotConfig:
    """Configurazione di esecuzione per Odoo PR Bot (Odoo 18.0, 19.0, 20.0)."""

    # Credenziali GitHub
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    github_webhook_secret: str = field(
        default_factory=lambda: os.getenv("GITHUB_WEBHOOK_SECRET", "")
    )
    github_api_url: str = field(
        default_factory=lambda: os.getenv("GITHUB_API_URL", "https://api.github.com")
    )

    # Comandi Bot (supporta sia /bot sia /ocabot)
    command_prefixes: list[str] = field(
        default_factory=lambda: [
            p.strip()
            for p in os.getenv("BOT_COMMAND_PREFIXES", "/bot,/ocabot").split(",")
            if p.strip()
        ]
    )

    # Serie Odoo supportate (default: 18.0, 19.0, 20.0)
    target_odoo_series: str = field(
        default_factory=lambda: os.getenv("TARGET_ODOO_SERIES", "18.0")
    )
    supported_odoo_series: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            s.strip()
            for s in os.getenv("SUPPORTED_ODOO_SERIES", "18.0,19.0,20.0").split(",")
            if s.strip()
        )
    )

    # Regole OCA Workflow
    required_approvals: int = field(
        default_factory=lambda: int(os.getenv("REQUIRED_APPROVALS", "1"))
    )
    ready_to_merge_min_days: int = field(
        default_factory=lambda: int(os.getenv("READY_TO_MERGE_MIN_DAYS", "5"))
    )
    auto_label: bool = field(
        default_factory=lambda: os.getenv("AUTO_LABEL", "true").lower()
        in ("1", "true", "yes")
    )
    mention_maintainers: bool = field(
        default_factory=lambda: os.getenv("MENTION_MAINTAINERS", "true").lower()
        in ("1", "true", "yes")
    )
    post_inline_comments: bool = field(
        default_factory=lambda: os.getenv("POST_INLINE_COMMENTS", "true").lower()
        in ("1", "true", "yes")
    )

    # Configurazione AI Code Review (opzionale: Gemini o OpenAI compatibile)
    ai_enabled: bool = field(
        default_factory=lambda: os.getenv("AI_REVIEW_ENABLED", "true").lower()
        in ("1", "true", "yes")
    )
    ai_provider: str = field(
        default_factory=lambda: os.getenv("AI_PROVIDER", "gemini").lower()
    )
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
    )
    ai_model: str = field(
        default_factory=lambda: os.getenv("AI_MODEL", "gemini-2.5-flash")
    )

    def resolve_series_for_branch(self, base_branch: str | None) -> str:
        """Determina la serie Odoo target in base al branch della PR (18.0, 19.0, 20.0)."""
        if base_branch:
            cleaned = base_branch.strip()
            if cleaned in self.supported_odoo_series:
                return cleaned
            for s in self.supported_odoo_series:
                if cleaned.startswith(s):
                    return s
        return self.target_odoo_series

    @property
    def has_ai_credentials(self) -> bool:
        """Verifica se le credenziali AI sono configurate e attive."""
        if not self.ai_enabled:
            return False
        if self.ai_provider == "gemini" and self.gemini_api_key:
            return True
        if self.ai_provider == "openai" and self.openai_api_key:
            return True
        return False
