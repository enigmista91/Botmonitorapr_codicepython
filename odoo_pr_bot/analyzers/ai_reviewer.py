"""Modulo per la Code Review assistita da AI su diff di moduli Odoo (18.0, 19.0, 20.0)."""

from __future__ import annotations

import logging
import httpx

from odoo_pr_bot.config import BotConfig
from odoo_pr_bot.models import ChangedFile

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """Sei un Senior Odoo & OCA Core Maintainer esperto nelle versioni Odoo 18.0, 19.0 e 20.0.
Analizza il diff della Pull Request (target Odoo {series}) e fornisci una code review sintetica in italiano, strutturata in bullet point, focalizzata su:
1. **Performance ORM Odoo**: query N+1 (`search`/`browse`/`write`/`create` dentro cicli `for`), mancato batching sui recordset, `@api.depends` incompleti o errati.
2. **Compatibilità Odoo {series} (18.0 / 19.0 / 20.0)**:
   - Uso di `_compute_display_name` invece di `name_get`.
   - Uso di `models.Constraint` invece di `_sql_constraints`.
   - Uso di `odoo.tools.SQL` per query raw sicure.
   - Viste XML con `<list>` invece di `<tree>`, `<chatter/>` invece di `oe_chatter`, ed espressioni dirette su `invisible`/`readonly`/`required` senza `attrs` o `states`.
3. **Sicurezza**: controlli ACL (`ir.model.access.csv`), Record Rules (`ir.rule`), uso improprio di `.sudo()`.

Se il codice è pulito e rispetta gli standard OCA per Odoo {series}, confermalo brevemente."""


async def run_ai_diff_review(
    changed_files: list[ChangedFile],
    config: BotConfig,
    target_series: str = "18.0",
) -> str | None:
    """Invia il diff dei file modificati al provider AI configurato (Gemini o OpenAI)."""
    if not config.has_ai_credentials:
        return None

    patches: list[str] = []
    total_chars = 0
    for cf in changed_files:
        if not cf.patch:
            continue
        snippet = f"--- File: {cf.filename} ({cf.status}) ---\n{cf.patch}\n"
        if total_chars + len(snippet) > 24000:
            patches.append("\n[... Diff troncato per limiti di contesto ...]")
            break
        patches.append(snippet)
        total_chars += len(snippet)

    if not patches:
        return None

    diff_payload = "\n".join(patches)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(series=target_series)

    try:
        if config.ai_provider == "gemini":
            return await _call_gemini(system_prompt, diff_payload, config)
        if config.ai_provider == "openai":
            return await _call_openai(system_prompt, diff_payload, config)
    except Exception as exc:
        logger.warning("Errore durante l'analisi AI della PR: %s", exc)
    return None


async def _call_gemini(
    system_prompt: str,
    diff_payload: str,
    config: BotConfig,
) -> str | None:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.ai_model}:generateContent?key={config.gemini_api_key}"
    )
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": f"Ecco il diff della PR per Odoo:\n\n```diff\n{diff_payload}\n```"
                    }
                ],
            }
        ],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts).strip() or None
    return None


async def _call_openai(
    system_prompt: str,
    diff_payload: str,
    config: BotConfig,
) -> str | None:
    url = f"{config.openai_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.openai_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.ai_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Ecco il diff della PR per Odoo:\n\n```diff\n{diff_payload}\n```",
            },
        ],
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if choices:
            return choices[0].get("message", {}).get("content", "").strip() or None
    return None
