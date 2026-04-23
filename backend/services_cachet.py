# /backend/services_cachet.py

from __future__ import annotations

import os
import re
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
import requests


# -------------------------
# Environment loading (LOCAL-ONLY)
# - In production (e.g. Render), do NOT depend on backend/.env.
# - Locally, load backend/.env if present.
# -------------------------

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def _is_truthy_env(name: str) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _should_load_dotenv() -> bool:
    """
    Loads backend/.env only for local/dev usage.

    Production should rely on real environment variables (Render dashboard).
    Detection rules:
      - If RENDER is set/truthy OR ENV/ENVIRONMENT indicates production -> don't load .env
      - Otherwise, if backend/.env exists -> load .env
    """
    if _is_truthy_env("RENDER"):
        return False

    env = (os.getenv("ENV", "") or "").strip().lower()
    environment = (os.getenv("ENVIRONMENT", "") or "").strip().lower()

    if env in {"prod", "production"} or environment in {"prod", "production"}:
        return False

    return ENV_PATH.exists()


if _should_load_dotenv():
    # override=False ensures real environment variables always win
    load_dotenv(dotenv_path=ENV_PATH, override=False)


# -------------------------
# Phase 1: Safe decoding + deterministic text cleanup
# -------------------------

_MOJIBAKE_MARKERS = ("Ã", "Â", "�", "├", "╬", "╣", "╚", "╔")


def _normalize_nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _looks_like_mojibake(value: str) -> bool:
    if not value:
        return False
    return any(m in value for m in _MOJIBAKE_MARKERS)


def _try_fix_mojibake(value: str) -> str:
    """
    Deterministically attempt to repair common mojibake patterns.

    Covers:
      - UTF-8 bytes decoded as latin-1/cp1252 => 'BollnÃ¤s' -> 'Bollnäs'
      - UTF-8 bytes shown as CP437 box drawing => 'Bolln├ñs' -> 'Bollnäs'

    Only applies when markers are present; otherwise returns NFC normalized original.
    """
    original = _normalize_nfc(value)

    if not _looks_like_mojibake(original):
        return original

    candidates: List[str] = []
    for enc in ("latin-1", "cp1252", "cp437"):
        try:
            repaired = value.encode(enc, errors="strict").decode("utf-8", errors="strict")
            candidates.append(_normalize_nfc(repaired))
        except UnicodeError:
            continue

    for cand in candidates:
        if cand != original:
            return cand

    return original


def _clean_text(value: Any) -> Any:
    """
    Clean/normalize only strings; pass through non-strings unchanged.
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return ""
    s = _normalize_nfc(s)
    s = _try_fix_mojibake(s)
    return s


def _clean_obj(obj: Any) -> Any:
    """
    Recursively clean strings inside dict/list payloads.
    """
    if isinstance(obj, dict):
        return {k: _clean_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_obj(x) for x in obj]
    return _clean_text(obj)


def _extract_declared_charset(resp: requests.Response) -> Optional[str]:
    ct = resp.headers.get("Content-Type") or resp.headers.get("content-type") or ""
    if not ct:
        return None
    m = re.search(r"charset\s*=\s*([^\s;]+)", ct, flags=re.IGNORECASE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _decode_http_body(content: bytes, declared_charset: Optional[str]) -> str:
    """
    Decode HTTP response bytes safely and deterministically:
      1) declared charset strict (if present)
      2) utf-8 strict
      3) latin-1 strict
    """
    if declared_charset:
        try:
            return content.decode(declared_charset, errors="strict")
        except UnicodeError:
            pass

    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeError:
        return content.decode("latin-1", errors="strict")


def _cachet_json(resp: requests.Response) -> Dict[str, Any]:
    """
    Parse Cachet JSON from raw bytes to avoid requests' encoding guessing.
    """
    declared = _extract_declared_charset(resp)
    raw_text = _decode_http_body(resp.content, declared)
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse Cachet response as JSON: {e}")

    if not isinstance(parsed, dict):
        raise RuntimeError("Unexpected Cachet response format (expected JSON object)")
    return parsed


# -------------------------
# Existing business logic
# -------------------------

def infer_state(status_name: str | None, message: str | None, human_status: str | None = None) -> str:
    """
    Determine incident state (active/resolved) with deterministic rules.

    Cachet fields we may see:
      - status_name (sometimes empty)
      - human_status (e.g. "Löst" in Swedish)
      - message text
    """
    s = (status_name or "").strip().lower()
    hs = (human_status or "").strip().lower()
    m = (message or "").strip().lower()

    # Strong signals from status fields
    if s in {"resolved", "fixed", "closed", "completed", "ok"}:
        return "resolved"

    # Cachet Swedish human_status examples: "Löst"
    if hs in {"löst", "lost", "resolved", "fixed", "closed", "completed", "ok"}:
        return "resolved"

    # Rule-based inference from message text (brittle but deterministic)
    resolved_markers = [
        "åtgärdat",
        "tjänster återställda",
        "tjanster aterstallda",
        "återställd",
        "aterstalld",
        "resolved",
        "restored",
        "fixed",
        "problemet är löst",
        "problemet ar lost",
        "tjänsten fungerar som vanligt igen",
        "tjansten fungerar som vanligt igen",
        "internet är tillbaka",
        "internet ar tillbaka",
    ]
    if any(x in m for x in resolved_markers):
        return "resolved"

    return "active"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_cachet_dt(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s.strip():
        return None
    s = s.strip()
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _normalize_postal_code(raw: str) -> Optional[str]:
    """
    Sweden postal codes commonly appear as:
      - 12345
      - 123 45
    We standardize to '123 45' for consistent display.
    """
    s = (raw or "").strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) != 5:
        return None
    return f"{digits[:3]} {digits[3:]}"


def _extract_postal_codes(text: str) -> List[str]:
    """
    Extract Swedish postal codes from text.

    Default regex matches:
      - 12345
      - 123 45

    You can override via POSTAL_CODE_REGEX.
    """
    pattern = os.getenv("POSTAL_CODE_REGEX", r"\b\d{3}\s?\d{2}\b")
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(r"\b\d{3}\s?\d{2}\b")

    out: List[str] = []
    seen = set()

    for m in rx.findall(text or ""):
        norm = _normalize_postal_code(m)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)

    return out


def _get_cachet_config() -> Dict[str, str]:
    base_url = os.getenv("CACHET_BASE_URL", "").strip()
    token = os.getenv("CACHET_API_TOKEN", "").strip()
    if not base_url or not token:
        raise RuntimeError("Cachet is not configured. Set CACHET_BASE_URL and CACHET_API_TOKEN.")
    return {"base_url": base_url.rstrip("/"), "token": token}


def _cachet_headers(token: str) -> Dict[str, str]:
    return {"X-Cachet-Token": token, "Accept": "application/json"}


def fetch_components() -> List[Dict[str, Any]]:
    cfg = _get_cachet_config()
    url = f"{cfg['base_url']}/api/v1/components"
    resp = requests.get(url, headers=_cachet_headers(cfg["token"]), timeout=15)
    resp.raise_for_status()
    payload = _cachet_json(resp)
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Cachet components response format")

    cleaned = _clean_obj(data)
    return cleaned if isinstance(cleaned, list) else data


def fetch_recent_incidents(per_page: int = 200) -> List[Dict[str, Any]]:
    cfg = _get_cachet_config()
    url = f"{cfg['base_url']}/api/v1/incidents"
    resp = requests.get(
        url,
        headers=_cachet_headers(cfg["token"]),
        params={"sort": "id", "order": "desc", "per_page": per_page},
        timeout=15,
    )
    resp.raise_for_status()
    payload = _cachet_json(resp)
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Cachet incidents response format")

    cleaned = _clean_obj(data)
    return cleaned if isinstance(cleaned, list) else data


def normalize_provider_key(name: str) -> str:
    # Ensure keying is based on clean text (defensive).
    s = str(_clean_text(name) or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s or "unknown-provider"


def _cachet_fallback_provider_key(component_id: Any) -> str:
    """
    If Cachet incident does not provide a component name, do NOT guess provider name
    from incident title/message (that creates bad providers).
    Use a stable fallback key.
    """
    try:
        cid = int(component_id)
        return f"cachet-component-{cid}"
    except Exception:
        return "cachet-component-unknown"


def map_cachet_incident_to_record(inc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map Cachet incident payload -> normalized record for DB ingest.

    IMPORTANT:
    - Provider identity should come from Cachet component name if available.
    - If component name is missing, fall back to a stable Cachet-based key;
      do NOT guess provider names from incident title/message.
    - Postal codes are extracted from BOTH incident name and incident message.
    """
    comp_name = ""
    comp_id = inc.get("component_id")

    # Cachet may include components list (some deployments do)
    comps = inc.get("components")
    if isinstance(comps, list) and comps:
        c0 = comps[0]
        if isinstance(c0, dict):
            comp_name = str(_clean_text(c0.get("name") or "")).strip()
            if comp_id is None:
                comp_id = c0.get("id")

    incident_name = str(_clean_text(inc.get("name") or "")).strip()
    msg = str(_clean_text(inc.get("message") or "")).strip()
    human_status = str(_clean_text(inc.get("human_status") or "")).strip()

    # Provider identity from component name when possible
    if comp_name:
        provider_key = normalize_provider_key(comp_name)
        display_name = comp_name
    else:
        # ✅ Stable fallback key; prevents polluting providers with incorrect names
        provider_key = _cachet_fallback_provider_key(comp_id)
        display_name = provider_key

    combined_text = f"{incident_name}\n{msg}".strip()
    codes = _extract_postal_codes(combined_text)

    created = _parse_cachet_dt(inc.get("created_at")) or _utc_now()
    updated = _parse_cachet_dt(inc.get("updated_at")) or created

    status_name = str(_clean_text(inc.get("status_name") or "")).strip() or None
    state = infer_state(status_name, combined_text or None, human_status or None)

    return {
        "source": "cachet",
        "external_id": str(inc.get("id")),
        "provider_key": provider_key,
        "display_name": display_name,
        "cachet_component_id": int(comp_id) if comp_id is not None else None,
        "status_name": status_name,
        "message": msg or None,
        "postal_codes": codes,
        "created_at_utc": created,
        "updated_at_utc": updated,
        "state": state,
    }