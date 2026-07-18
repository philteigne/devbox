from __future__ import annotations

from typing import Any


def has_required_review(protection: dict[str, Any] | None) -> bool:
    if not protection:
        return False
    reviews = protection.get("required_pull_request_reviews") or {}
    count = reviews.get("required_approving_review_count") or 0
    try:
        return int(count) >= 1
    except (TypeError, ValueError):
        return False


def app_bypasses_reviews(protection: dict[str, Any] | None, app_slug: str) -> bool:
    if not protection or not app_slug:
        return False
    reviews = protection.get("required_pull_request_reviews") or {}
    allowances = reviews.get("bypass_pull_request_allowances") or {}
    for app in allowances.get("apps") or []:
        values = []
        if isinstance(app, str):
            values.append(app)
        elif isinstance(app, dict):
            values.extend(str(app.get(key) or "") for key in ("slug", "name", "login"))
        if any(value.casefold() == app_slug.casefold() for value in values if value):
            return True
    return False


def build_payload(existing: dict[str, Any] | None) -> dict[str, Any]:
    existing = existing or {}
    reviews = _pull_request_reviews(existing.get("required_pull_request_reviews") or {})
    reviews["required_approving_review_count"] = max(
        int(reviews.get("required_approving_review_count") or 0),
        1,
    )
    return {
        "required_status_checks": _status_checks(existing.get("required_status_checks")),
        "enforce_admins": bool((existing.get("enforce_admins") or {}).get("enabled", False)),
        "required_pull_request_reviews": reviews,
        "restrictions": _restrictions(existing.get("restrictions")),
        "required_linear_history": bool((existing.get("required_linear_history") or {}).get("enabled", False)),
        "allow_force_pushes": bool((existing.get("allow_force_pushes") or {}).get("enabled", False)),
        "allow_deletions": bool((existing.get("allow_deletions") or {}).get("enabled", False)),
        "block_creations": bool((existing.get("block_creations") or {}).get("enabled", False)),
        "required_conversation_resolution": bool((existing.get("required_conversation_resolution") or {}).get("enabled", False)),
        "lock_branch": bool((existing.get("lock_branch") or {}).get("enabled", False)),
        "allow_fork_syncing": bool((existing.get("allow_fork_syncing") or {}).get("enabled", False)),
    }


def _status_checks(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    checks = []
    for check in value.get("checks") or []:
        if not check.get("context"):
            continue
        item = {"context": check.get("context")}
        if check.get("app_id") is not None:
            item["app_id"] = check.get("app_id")
        checks.append(item)
    return {
        "strict": bool(value.get("strict", False)),
        "contexts": value.get("contexts") or [],
        "checks": checks,
    }


def _pull_request_reviews(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dismiss_stale_reviews": bool(value.get("dismiss_stale_reviews", False)),
        "require_code_owner_reviews": bool(value.get("require_code_owner_reviews", False)),
        "required_approving_review_count": int(value.get("required_approving_review_count") or 0),
        "require_last_push_approval": bool(value.get("require_last_push_approval", False)),
    }
    dismissal = _users_teams_apps(value.get("dismissal_restrictions"))
    if dismissal is not None:
        result["dismissal_restrictions"] = dismissal
    bypass = _users_teams_apps(value.get("bypass_pull_request_allowances"))
    if bypass is not None:
        result["bypass_pull_request_allowances"] = bypass
    return result


def _restrictions(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return _users_teams_apps(value)


def _users_teams_apps(value: dict[str, Any] | None) -> dict[str, list[str]] | None:
    if value is None:
        return None
    return {
        "users": [_login(item) for item in value.get("users") or [] if _login(item)],
        "teams": [_slug(item) for item in value.get("teams") or [] if _slug(item)],
        "apps": [_slug(item) for item in value.get("apps") or [] if _slug(item)],
    }


def _login(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("login") or "")
    return ""


def _slug(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("slug") or item.get("name") or "")
    return ""
