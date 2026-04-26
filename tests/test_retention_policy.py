from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from nextballup_core.settings import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
RETENTION_POLICY = REPO_ROOT / "docs" / "privacy" / "retention_policy.yaml"
WORKER_SRC = REPO_ROOT / "apps" / "worker" / "src" / "nextballup_worker"
_NON_CODE_ACTIONS = {"none", "pending_implementation"}
_ALLOWED_UNITS = {"minutes", "days"}


def test_retention_policy_actions_match_worker_cleanup_tasks() -> None:
    raw = yaml.safe_load(RETENTION_POLICY.read_text())
    assert isinstance(raw, list)
    cleanup_sources = "\n".join(path.read_text() for path in WORKER_SRC.rglob("*.py"))
    kinds: set[str] = set()
    for entry in raw:
        assert isinstance(entry, dict)
        typed = dict[str, Any](entry)
        kind = typed.get("kind")
        retention_days = typed.get("retention_days")
        retention_source = typed.get("retention_source")
        deletion_action = typed.get("deletion_action")
        assert isinstance(kind, str) and kind
        assert kind not in kinds
        kinds.add(kind)
        if retention_days is not None:
            assert isinstance(retention_days, int) and retention_days > 0
        else:
            assert isinstance(retention_source, str) and retention_source
            settings_field = retention_source.lower()
            assert settings_field in Settings.model_fields
            assert retention_source in (REPO_ROOT / ".env.example").read_text()
        assert typed.get("retention_unit") in _ALLOWED_UNITS
        assert isinstance(typed.get("justification"), str) and typed["justification"]
        assert isinstance(deletion_action, str) and deletion_action
        if deletion_action not in _NON_CODE_ACTIONS:
            assert f"def {deletion_action}(" in cleanup_sources
