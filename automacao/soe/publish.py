"""Publicacao do data.json no GitHub via git (subprocess).

Idempotente: se nada mudou no working tree, NAO commita nem faz push. Respeita
--no-publish (controlado pelo orquestrador). Falha graciosa (loga e retorna
False) para nao derrubar o pipeline.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

logger = logging.getLogger("soe.publish")


def _git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120
    )


def _has_changes(repo: Path, paths: List[str]) -> bool:
    """True se algum dos paths tem mudancas staged/unstaged."""
    res = _git(["status", "--porcelain", "--", *paths], repo)
    return bool(res.stdout.strip())


def publish(payload: Dict[str, Any], out_path: Path,
            extra_paths: Optional[List[str]] = None) -> bool:
    """git add/commit/push do data.json (e arquivos extras). Retorna True se publicou."""
    repo = config.REPO_DIR
    meta = payload.get("meta", {})
    rel = str(Path(out_path).resolve().relative_to(repo)) if _under(out_path, repo) else str(out_path)
    paths = [rel] + (extra_paths or [])

    try:
        if not (repo / ".git").exists():
            logger.warning("Sem repositorio git em %s; publicacao ignorada.", repo)
            return False

        if not _has_changes(repo, paths):
            logger.info("Nada mudou em %s; commit/push ignorados (idempotente).", paths)
            return False

        add = _git(["add", *paths], repo)
        if add.returncode != 0:
            logger.error("git add falhou: %s", add.stderr.strip())
            return False

        # Re-checa apos add (caso diff seja apenas reordenacao identica)
        staged = _git(["diff", "--cached", "--quiet"], repo)
        if staged.returncode == 0:
            logger.info("Sem diff staged; commit ignorado.")
            return False

        msg = (f"data: atualizacao S&OE {meta.get('run_label')} "
               f"{meta.get('generated_at_brt')} (v{meta.get('version')})")
        commit = _git(["commit", "-m", msg], repo)
        if commit.returncode != 0:
            logger.error("git commit falhou: %s", commit.stderr.strip() or commit.stdout.strip())
            return False

        branch = config.SETTINGS.git_branch
        push = _git(["push", "origin", branch], repo)
        if push.returncode != 0:
            logger.error("git push falhou: %s", push.stderr.strip())
            return False

        logger.info("Publicado: %s", msg)
        return True
    except Exception as exc:  # noqa: BLE001 - falha graciosa por design
        logger.error("Falha na publicacao (pipeline segue): %s", exc)
        return False


def _under(path: Path, base: Path) -> bool:
    try:
        Path(path).resolve().relative_to(base)
        return True
    except ValueError:
        return False
