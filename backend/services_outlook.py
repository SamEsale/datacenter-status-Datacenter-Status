# /backend/services_outlook.py

from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import msal
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
# Safe text normalization (matches Phase 1/2 strategy)
# -------------------------

_MOJIBAKE_MARKERS = ("Ã", "Â", "�", "├", "╬", "╣", "╚", "╔")


def _normalize_nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _looks_like_mojibake(value: str) -> bool:
    return bool(value) and any(m in value for m in _MOJIBAKE_MARKERS)


def _try_fix_mojibake(value: str) -> str:
    original = _normalize_nfc(value)
    if not _looks_like_mojibake(original):
        return original

    for enc in ("latin-1", "cp1252", "cp437"):
        try:
            repaired = value.encode(enc, errors="strict").decode("utf-8", errors="strict")
            repaired = _normalize_nfc(repaired)
            if repaired != original:
                return repaired
        except UnicodeError:
            continue

    return original


def _clean_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    s = value.strip()
    if not s:
        return ""
    s = _normalize_nfc(s)
    s = _try_fix_mojibake(s)
    return s


# -------------------------
# Time + Graph auth
# -------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_graph_dt(value: Any) -> datetime:
    try:
        s = str(value).strip()
        if not s:
            return _utc_now()
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return _utc_now()


def _get_graph_token() -> str:
    tenant = os.getenv("AZURE_TENANT_ID", "").strip()
    client_id = os.getenv("AZURE_CLIENT_ID", "").strip()
    client_secret = os.getenv("AZURE_CLIENT_SECRET", "").strip()

    if not tenant or not client_id or not client_secret:
        raise RuntimeError(
            "Graph is not configured. Set AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET."
        )

    authority = f"https://login.microsoftonline.com/{tenant}"
    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
    )

    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire Graph token: {result.get('error_description') or result}")

    return str(result["access_token"])


def _graph_get_json(url: str, token: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=25)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Graph response format (expected JSON object)")
    return payload


# -------------------------
# HTML -> text
# -------------------------

def _strip_html_if_needed(content: str, content_type: str | None) -> str:
    if not content:
        return ""
    ct = (content_type or "").lower()
    if "html" not in ct:
        return content

    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", content)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


# -------------------------
# State inference
# -------------------------

def infer_state_from_outlook(subject: str, body_text: str) -> str:
    """
    Deterministic inference of active/resolved from Outlook text.
    """
    s = f"{subject}\n{body_text}".lower()

    resolved_markers = [
        # Swedish
        "åtgärdat",
        "återställd",
        "återställt",
        "tjänsten fungerar",
        "tjänster återställda",
        "problemet är löst",
        "internet är tillbaka",
        "åter i drift",
        "i drift igen",
        "stängts",
        "stängd",
        "avslutad",
        "avslutats",
        "är avslutad",
        "är stängd",
        # English
        "resolved",
        "restored",
        "fixed",
        "closed",
        "completed",
    ]
    if any(m in s for m in resolved_markers):
        return "resolved"

    return "active"


# -------------------------
# Postal codes (Sweden)
# -------------------------

def _normalize_postal_code(raw: str) -> Optional[str]:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) != 5:
        return None
    return f"{digits[:3]} {digits[3:]}"


def _extract_postal_codes(text: str) -> List[str]:
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


# -------------------------
# Provider extraction + naming
# (IMPORTANT: do NOT derive provider from subject prefix; that created junk providers)
# -------------------------

def _normalize_provider_key(name: str) -> str:
    s = str(_clean_text(name) or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s or "unknown-provider"


def _load_known_providers_from_env() -> List[str]:
    raw = os.getenv("KNOWN_PROVIDERS", "").strip()
    if not raw:
        return []
    items = [x.strip() for x in raw.split(",") if x and x.strip()]
    out: List[str] = []
    seen = set()
    for x in items:
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def _find_known_provider_in_text(text: str, known_providers: List[str]) -> Optional[str]:
    if not text or not known_providers:
        return None

    t = (text or "").lower()
    ordered = sorted(known_providers, key=lambda x: len(x), reverse=True)

    for name in ordered:
        n = name.lower().strip()
        if not n:
            continue

        rx = re.compile(
            rf"(?i)(^|[\s\(\[\{{,;:/\-\|\"']){re.escape(name)}($|[\s\)\]\}},;:/\-\|\"'.!?\r\n])"
        )
        if rx.search(text):
            return name

        if n in t:
            return name

    return None


def _extract_provider_display_name(subject: str, body_text: str) -> str:
    """
    Provider extraction order (deterministic):
      1) body line: Provider: X
      2) body line: Leverantör: X
      3) KNOWN_PROVIDERS match in subject+body (optional env)
      4) fallback: 'unknown-provider'

    NOTE: We intentionally DO NOT use subject prefix heuristics anymore.
    """
    sub = str(_clean_text(subject or "")).strip()
    body = str(_clean_text(body_text or "")).strip()

    m = re.search(r"(?im)^\s*provider\s*:\s*(.+?)\s*$", body)
    if m:
        cand = m.group(1).strip()
        if cand:
            return cand

    m = re.search(r"(?im)^\s*leverant[oö]r\s*:\s*(.+?)\s*$", body)
    if m:
        cand = m.group(1).strip()
        if cand:
            return cand

    known = _load_known_providers_from_env()
    hit = _find_known_provider_in_text(f"{sub}\n{body}", known)
    if hit:
        return hit

    return "unknown-provider"


def parse_outlook_message(subject: str, body_text: str) -> Tuple[str, str, str, List[str]]:
    """
    Returns:
      provider_key (normalized identity key),
      display_name (pretty provider name),
      message (text to store),
      postal_codes (deduped list)
    """
    subject_c = str(_clean_text(subject or "")).strip()
    body_c = str(_clean_text(body_text or "")).strip()

    full_text = f"{subject_c}\n{body_c}".strip()
    postal_codes = _extract_postal_codes(full_text)

    display_name = _extract_provider_display_name(subject_c, body_c)
    provider_key = _normalize_provider_key(display_name)

    message = body_c or subject_c or "Outlook incident"
    return provider_key, display_name, message, postal_codes


# -------------------------
# Helpers: extract sender address/name from Graph message
# -------------------------

def _extract_sender_address(msg: Dict[str, Any]) -> Optional[str]:
    """
    Graph message contains:
      msg['from']['emailAddress']['address']
    Sometimes also msg['sender']...
    """
    if not isinstance(msg, dict):
        return None
    for key in ("from", "sender"):
        block = msg.get(key) or {}
        if isinstance(block, dict):
            ea = block.get("emailAddress") or {}
            if isinstance(ea, dict):
                addr = str(ea.get("address") or "").strip()
                if addr:
                    return str(_clean_text(addr)).strip().lower()
    return None


def _extract_sender_name(msg: Dict[str, Any]) -> Optional[str]:
    if not isinstance(msg, dict):
        return None
    for key in ("from", "sender"):
        block = msg.get(key) or {}
        if isinstance(block, dict):
            ea = block.get("emailAddress") or {}
            if isinstance(ea, dict):
                name = str(ea.get("name") or "").strip()
                if name:
                    return str(_clean_text(name)).strip()
    return None


# -------------------------
# Fetch incidents from Outlook / Graph
# -------------------------

def fetch_recent_outlook_incidents() -> List[Dict[str, Any]]:
    """
    Fetch incidents from Outlook inbox (Graph API).

    Env vars:
      - OUTLOOK_MAILBOX_USER (required): mailbox UPN/email
      - OUTLOOK_FETCH_TOP (optional, default 25)
      - OUTLOOK_MAX_PAGES (optional, default 4) pagination limit
      - OUTLOOK_MAX_AGE_HOURS (optional, default 72) restricts by receivedDateTime
      - POSTAL_CODE_REGEX (optional): default \\b\\d{3}\\s?\\d{2}\\b
      - KNOWN_PROVIDERS (optional): comma-separated provider names for matching
    """
    mailbox = os.getenv("OUTLOOK_MAILBOX_USER", "").strip()
    if not mailbox:
        raise RuntimeError("OUTLOOK_MAILBOX_USER is not set (UPN/email of the monitored mailbox).")

    token = _get_graph_token()

    top = int(os.getenv("OUTLOOK_FETCH_TOP", "25"))
    max_pages = int(os.getenv("OUTLOOK_MAX_PAGES", "4"))
    max_age_hours = int(os.getenv("OUTLOOK_MAX_AGE_HOURS", os.getenv("INCIDENT_MAX_AGE_HOURS", "72")))

    since_dt = _utc_now() - timedelta(hours=max_age_hours)
    since_iso = since_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    url = f"https://graph.microsoft.com/v1.0/users/{mailbox}/mailFolders/Inbox/messages"
    params: Dict[str, Any] = {
        "$top": top,
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,receivedDateTime,body,from,sender",
        "$filter": f"receivedDateTime ge {since_iso}",
    }

    out: List[Dict[str, Any]] = []
    pages = 0

    while url and pages < max_pages:
        payload = _graph_get_json(url, token, params=params if pages == 0 else None)
        items = payload.get("value") or []
        if not isinstance(items, list):
            break

        for it in items:
            if not isinstance(it, dict):
                continue

            msg_id = str(it.get("id") or "").strip()
            if not msg_id:
                continue

            subject_raw = str(_clean_text(it.get("subject") or ""))
            received_dt = _parse_graph_dt(it.get("receivedDateTime"))

            from_address = _extract_sender_address(it)
            from_name = _extract_sender_name(it)

            body = it.get("body") or {}
            body_content = str((body or {}).get("content") or "")
            body_type = str((body or {}).get("contentType") or "")

            body_text = _strip_html_if_needed(body_content, body_type)
            body_text = str(_clean_text(body_text))

            provider_key, display_name, message, postal_codes = parse_outlook_message(subject_raw, body_text)

            state = infer_state_from_outlook(subject_raw, body_text)
            status_name = "Down" if state == "active" else "Resolved"

            out.append(
                {
                    "source": "outlook",
                    "external_id": msg_id,
                    "provider_key": provider_key,
                    "display_name": display_name,
                    "state": state,
                    "status_name": status_name,
                    "message": message,
                    "postal_codes": postal_codes,
                    "updated_at_utc": received_dt,
                    "created_at_utc": received_dt,
                    # NEW: audit/traceability fields
                    "from_address": from_address,
                    "from_name": from_name,
                    "subject": subject_raw,
                }
            )

        url = str(payload.get("@odata.nextLink") or "").strip()
        pages += 1

    return out