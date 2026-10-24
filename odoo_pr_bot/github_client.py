"""Client GitHub REST API v3 per l'ispezione delle PR, gestione label, commenti e merge."""

from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
from typing import Any
import httpx

from odoo_pr_bot.analyzers.manifest_analyzer import bump_manifest_version
from odoo_pr_bot.config import BotConfig
from odoo_pr_bot.models import ChangedFile, PRContext, ReviewFinding, Severity

logger = logging.getLogger(__name__)


class GitHubClient:
    """Client asincrono per interagire con le API GitHub di un repository Odoo."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.base_url = config.github_api_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "odoo-pr-bot/1.0.0",
        }
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token}"
        return headers

    async def build_pr_context(self, repo_full_name: str, pr_number: int) -> PRContext:
        """Scarica metadati PR, file modificati, contenuti raw, approvazioni e stato CI."""
        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers(), timeout=30.0
        ) as client:
            # 1. Dettagli PR
            pr_resp = await client.get(f"/repos/{repo_full_name}/pulls/{pr_number}")
            pr_resp.raise_for_status()
            pr_data = pr_resp.json()

            head_sha = pr_data["head"]["sha"]
            base_branch = pr_data["base"]["ref"]
            head_branch = pr_data["head"]["ref"]
            head_repo_full_name = (
                (pr_data["head"].get("repo") or {}).get("full_name") or repo_full_name
            )
            current_labels = [lbl["name"] for lbl in pr_data.get("labels", [])]

            # 2. Revisioni (approvazioni / richieste modifiche)
            rev_resp = await client.get(
                f"/repos/{repo_full_name}/pulls/{pr_number}/reviews"
            )
            rev_resp.raise_for_status()
            latest_by_user: dict[str, str] = {}
            for rev in rev_resp.json():
                user_login = (rev.get("user") or {}).get("login", "")
                state = rev.get("state", "")
                if user_login and state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                    latest_by_user[user_login] = state

            approvals_count = sum(
                1 for s in latest_by_user.values() if s == "APPROVED"
            )
            changes_requested_count = sum(
                1 for s in latest_by_user.values() if s == "CHANGES_REQUESTED"
            )

            # 3. Stato CI sul commit head
            ci_state = await self._fetch_ci_state(client, repo_full_name, head_sha)

            # 4. File modificati nella PR
            files_resp = await client.get(
                f"/repos/{repo_full_name}/pulls/{pr_number}/files",
                params={"per_page": 100},
            )
            files_resp.raise_for_status()
            changed_files: list[ChangedFile] = []

            for f_item in files_resp.json():
                filename = f_item["filename"]
                status = f_item.get("status", "modified")
                patch = f_item.get("patch", "")
                content: str | None = None

                if status != "removed" and filename.endswith(
                    (".py", ".xml", ".csv", ".rst", ".md", ".yaml", ".yml")
                ):
                    content = await self._fetch_file_content(
                        client, repo_full_name, filename, head_sha
                    )

                changed_files.append(
                    ChangedFile(
                        filename=filename,
                        status=status,
                        patch=patch,
                        content=content,
                        additions=f_item.get("additions", 0),
                        deletions=f_item.get("deletions", 0),
                    )
                )

            return PRContext(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                title=pr_data.get("title", ""),
                body=pr_data.get("body") or "",
                author=(pr_data.get("user") or {}).get("login", ""),
                base_branch=base_branch,
                head_branch=head_branch,
                head_sha=head_sha,
                head_repo_full_name=head_repo_full_name,
                is_draft=bool(pr_data.get("draft", False)),
                created_at_iso=pr_data.get("created_at"),
                current_labels=current_labels,
                approvals_count=approvals_count,
                changes_requested_count=changes_requested_count,
                ci_state=ci_state,
                changed_files=changed_files,
            )

    async def _fetch_ci_state(
        self, client: httpx.AsyncClient, repo_full_name: str, sha: str
    ) -> str:
        """Verifica lo stato combinato dei check GitHub Actions / Statuses."""
        try:
            checks_resp = await client.get(
                f"/repos/{repo_full_name}/commits/{sha}/check-runs"
            )
            if checks_resp.status_code == 200:
                runs = checks_resp.json().get("check_runs", [])
                if runs:
                    if any(
                        r.get("conclusion") in ("failure", "timed_out", "cancelled")
                        for r in runs
                    ):
                        return "failure"
                    if all(
                        r.get("status") == "completed"
                        and r.get("conclusion") in ("success", "skipped", "neutral")
                        for r in runs
                    ):
                        return "success"
                    return "pending"
        except Exception as exc:
            logger.debug("Impossibile leggere check-runs per %s: %s", sha, exc)
        return "pending"

    async def _fetch_file_content(
        self,
        client: httpx.AsyncClient,
        repo_full_name: str,
        path: str,
        ref: str,
    ) -> str | None:
        """Scarica il contenuto testuale di un file a uno specifico commit ref."""
        try:
            resp = await client.get(
                f"/repos/{repo_full_name}/contents/{path}", params={"ref": ref}
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("encoding") == "base64" and "content" in data:
                raw_bytes = base64.b64decode(data["content"])
                return raw_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.debug("Impossibile scaricare il file %s@%s: %s", path, ref, exc)
        return None

    async def post_issue_comment(
        self, repo_full_name: str, pr_number: int, body: str
    ) -> dict[str, Any]:
        """Pubblica un commento generale sulla PR."""
        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers(), timeout=20.0
        ) as client:
            resp = await client.post(
                f"/repos/{repo_full_name}/issues/{pr_number}/comments",
                json={"body": body},
            )
            resp.raise_for_status()
            return resp.json()

    async def post_inline_review(
        self,
        pr: PRContext,
        findings: list[ReviewFinding],
        summary_body: str,
    ) -> None:
        """Invia una GitHub Pull Request Review con commenti inline sulle righe modificate."""
        inline_comments: list[dict[str, Any]] = []
        changed_paths = {cf.filename for cf in pr.changed_files if cf.patch}

        for f in findings:
            if (
                f.file_path in changed_paths
                and f.line
                and f.line > 0
                and f.severity in (Severity.ERROR, Severity.WARNING)
            ):
                comment_text = f"**[{f.rule_id}]** ({f.severity.value.upper()}): {f.message}"
                if f.suggestion:
                    comment_text += f"\n\nSuggerimento:\n```suggestion\n{f.suggestion}\n```"
                inline_comments.append(
                    {
                        "path": f.file_path,
                        "line": f.line,
                        "side": "RIGHT",
                        "body": comment_text,
                    }
                )

        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers(), timeout=25.0
        ) as client:
            payload: dict[str, Any] = {
                "commit_id": pr.head_sha,
                "body": summary_body,
                "event": "COMMENT",
            }
            if inline_comments and self.config.post_inline_comments:
                payload["comments"] = inline_comments[:30]

            resp = await client.post(
                f"/repos/{pr.repo_full_name}/pulls/{pr.pr_number}/reviews",
                json=payload,
            )
            if resp.status_code >= 400:
                # Fallback a commento standard se le righe inline non appartengono all'hunk del diff
                await self.post_issue_comment(
                    pr.repo_full_name, pr.pr_number, summary_body
                )

    async def apply_label_changes(
        self,
        repo_full_name: str,
        pr_number: int,
        labels_to_add: list[str],
        labels_to_remove: list[str],
    ) -> None:
        """Applica e rimuove le label OCA sulla PR."""
        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers(), timeout=20.0
        ) as client:
            if labels_to_add:
                await client.post(
                    f"/repos/{repo_full_name}/issues/{pr_number}/labels",
                    json={"labels": labels_to_add},
                )
            for lbl in labels_to_remove:
                await client.delete(
                    f"/repos/{repo_full_name}/issues/{pr_number}/labels/{lbl}"
                )

    async def bump_pr_manifests(
        self,
        pr: PRContext,
        bump_type: str,
    ) -> list[tuple[str, str, str]]:
        """Incrementa la versione in tutti i __manifest__.py dei moduli modificati nella PR."""
        target_series = self.config.resolve_series_for_branch(pr.base_branch)
        bumped: list[tuple[str, str, str]] = []

        # Individua i percorsi dei manifest toccati o appartenenti ai moduli toccati
        manifest_paths: set[str] = set()
        for cf in pr.changed_files:
            if cf.status == "removed":
                continue
            parts = cf.filename.strip("/").split("/")
            if parts[0] in ("addons", "custom_addons") and len(parts) >= 2:
                manifest_paths.add(f"{parts[0]}/{parts[1]}/__manifest__.py")
            elif len(parts) >= 2 and not parts[0].startswith("."):
                manifest_paths.add(f"{parts[0]}/__manifest__.py")

        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers(), timeout=25.0
        ) as client:
            for m_path in sorted(manifest_paths):
                resp = await client.get(
                    f"/repos/{pr.repo_full_name}/contents/{m_path}",
                    params={"ref": pr.head_branch},
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                file_sha = data["sha"]
                raw_content = base64.b64decode(data["content"]).decode(
                    "utf-8", errors="replace"
                )
                try:
                    new_content, old_v, new_v = bump_manifest_version(
                        raw_content,
                        bump_type=bump_type,
                        target_odoo_series=target_series,
                    )
                except ValueError:
                    continue

                put_resp = await client.put(
                    f"/repos/{pr.repo_full_name}/contents/{m_path}",
                    json={
                        "message": f"[IMP] {m_path.split('/')[0]}: bump version {old_v} -> {new_v}",
                        "content": base64.b64encode(new_content.encode("utf-8")).decode(
                            "ascii"
                        ),
                        "sha": file_sha,
                        "branch": pr.head_branch,
                    },
                )
                if put_resp.status_code in (200, 201):
                    bumped.append((m_path, old_v, new_v))

        return bumped

    async def merge_pull_request(
        self,
        pr: PRContext,
        merge_method: str = "rebase",
    ) -> tuple[bool, str]:
        """Esegue il merge della PR e applica la label 'merged' in stile OCA."""
        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers(), timeout=25.0
        ) as client:
            resp = await client.put(
                f"/repos/{pr.repo_full_name}/pulls/{pr.pr_number}/merge",
                json={
                    "commit_title": f"{pr.title} (#{pr.pr_number})",
                    "merge_method": merge_method,
                },
            )
            if resp.status_code == 200:
                await self.apply_label_changes(
                    pr.repo_full_name,
                    pr.pr_number,
                    labels_to_add=["merged"],
                    labels_to_remove=["needs review", "ready to merge", "wip"],
                )
                return True, "Merge completato con successo."
            data = resp.json()
            return False, data.get("message", f"HTTP {resp.status_code}")

    async def rebase_pull_request(self, pr: PRContext) -> tuple[bool, str]:
        """Esegue il rebase del branch della PR sul base_branch in stile `/ocabot rebase`.

        1. Tenta prima un vero `git rebase` lineare + `git push --force-with-lease`
           (come `OCA/oca-github-bot`).
        2. Se l'ambiente non consente il push git diretto (es. assenza di git CLI),
           esegue il fallback all'endpoint GitHub API `/pulls/{pr}/update-branch`.
        """
        if self.config.github_token:
            ok, msg = self._git_cli_rebase(pr)
            if ok or "CONFLICT" in msg or "conflitto" in msg.lower():
                return ok, msg

        # Fallback API GitHub update-branch
        async with httpx.AsyncClient(
            base_url=self.base_url, headers=self._headers(), timeout=25.0
        ) as client:
            resp = await client.put(
                f"/repos/{pr.repo_full_name}/pulls/{pr.pr_number}/update-branch",
                json={"expected_head_sha": pr.head_sha},
            )
            if resp.status_code in (200, 202):
                return (
                    True,
                    f"Branch `{pr.head_branch}` aggiornato con successo rispetto a `{pr.base_branch}`.",
                )
            data = resp.json()
            return False, data.get("message", f"HTTP {resp.status_code}")

    def _git_cli_rebase(self, pr: PRContext) -> tuple[bool, str]:
        """Clona i branch in una cartella temporanea, esegue `git rebase` e `git push --force-with-lease`."""
        token = self.config.github_token
        base_repo_url = f"https://x-access-token:{token}@github.com/{pr.repo_full_name}.git"
        head_repo = pr.head_repo_full_name or pr.repo_full_name
        head_repo_url = f"https://x-access-token:{token}@github.com/{head_repo}.git"

        try:
            with tempfile.TemporaryDirectory(prefix="ocabot_rebase_") as tmpdir:
                def _run(args: list[str]) -> subprocess.CompletedProcess:
                    return subprocess.run(
                        args,
                        cwd=tmpdir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                        check=False,
                    )

                _run(["git", "init"])
                _run(["git", "config", "user.name", "odoo-pr-bot"])
                _run(["git", "config", "user.email", "odoo-pr-bot@users.noreply.github.com"])
                _run(["git", "remote", "add", "origin", base_repo_url])
                _run(["git", "remote", "add", "head_repo", head_repo_url])

                fetch_base = _run(["git", "fetch", "--depth=100", "origin", pr.base_branch])
                if fetch_base.returncode != 0:
                    return False, f"Impossibile scaricare `{pr.base_branch}`: {fetch_base.stderr.strip()}"

                fetch_head = _run(["git", "fetch", "--depth=100", "head_repo", pr.head_branch])
                if fetch_head.returncode != 0:
                    return False, f"Impossibile scaricare `{pr.head_branch}`: {fetch_head.stderr.strip()}"

                _run(["git", "checkout", "-B", pr.head_branch, "FETCH_HEAD"])

                rebase_proc = _run(["git", "rebase", f"origin/{pr.base_branch}"])
                if rebase_proc.returncode != 0:
                    _run(["git", "rebase", "--abort"])
                    return (
                        False,
                        f"Conflitto durante il rebase di `{pr.head_branch}` su `origin/{pr.base_branch}` "
                        "(CONFLICT). Risolvi i conflitti localmente ed esegui il push.",
                    )

                push_proc = _run(
                    [
                        "git",
                        "push",
                        "--force-with-lease",
                        "head_repo",
                        f"HEAD:refs/heads/{pr.head_branch}",
                    ]
                )
                if push_proc.returncode != 0:
                    return False, f"Errore durante il push del rebase: {push_proc.stderr.strip()}"

                return (
                    True,
                    f"Rebase di `{pr.head_branch}` su `{pr.base_branch}` completato e pushato con successo.",
                )
        except Exception as exc:
            logger.debug("Fallback da git CLI rebase: %s", exc)
            return False, str(exc)
