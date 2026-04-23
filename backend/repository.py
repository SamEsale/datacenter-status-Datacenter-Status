# /backend/repository.py

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import Incident, IncidentLocation, Provider, ProviderAlias


# -------------------------
# Phase 1 hardening: canonical text normalization at DB boundary
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


def _utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# -------------------------
# Swedish postal code normalization
# Canonical storage format: "NNN NN"
# -------------------------

_POSTAL_DIGITS_RE = re.compile(r"\D+")


def _postal_digits(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    return _POSTAL_DIGITS_RE.sub("", s)


def _format_swedish_postal_code(value: Any) -> Optional[str]:
    d = _postal_digits(value)
    if len(d) != 5:
        return None
    return f"{d[:3]} {d[3:]}"


# -------------------------
# Provider key hardening (prevents subject-derived "cs123456:" providers)
# -------------------------

_UNKNOWN_PROVIDER_KEY = "unknown-provider"

# IMPORTANT: keep the literal "cs[0-9]{6,}:" in this file so your inspect check works.
# We intentionally allow optional separators because the upstream may pass:
#   "cs7156540:"  OR  "cs7156540"  OR  "CS7156540 - ..." etc.
_CS_LIKE_PROVIDER_RE = re.compile(r"^cs[0-9]{6,}(?::|\b)", re.IGNORECASE)


def _looks_like_cs_provider_key(value: Optional[str]) -> bool:
    if not value:
        return False
    s = str(value).strip()
    if not s:
        return False
    return bool(_CS_LIKE_PROVIDER_RE.match(s))


def _normalize_provider_key(value: str) -> str:
    s = str(_clean_text(value) or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s or _UNKNOWN_PROVIDER_KEY


# -------------------------
# Provider display name precedence (Outlook primary)
# -------------------------

_PLACEHOLDER_NAMES = {
    "",
    "provider",
    "unknown",
    "unknown-provider",
    "unknown provider",
    "n/a",
    "na",
    "none",
    "-",
    "—",
}


def _normalize_name_for_compare(name: str) -> str:
    s = str(_clean_text(name or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _is_placeholder_display_name(name: str) -> bool:
    s = _normalize_name_for_compare(name).lower()
    if s in _PLACEHOLDER_NAMES:
        return True
    if s.startswith("unknown"):
        return True
    return False


def _looks_like_real_provider_name(name: str) -> bool:
    s = _normalize_name_for_compare(name)
    if not s:
        return False
    if _is_placeholder_display_name(s):
        return False
    return bool(re.search(r"[A-Za-zÅÄÖåäö]", s))


def _should_update_display_name(current: str, incoming: str) -> bool:
    """
    Outlook-first policy:
    - Never downgrade a real provider name.
    - Allow upgrading placeholder names.
    - Allow improvements (longer / better normalized names).
    - Prevent Cachet from overwriting Outlook naming.
    """
    cur = _normalize_name_for_compare(current)
    inc = _normalize_name_for_compare(incoming)

    if not inc or _is_placeholder_display_name(inc):
        return False

    if not cur or _is_placeholder_display_name(cur):
        return _looks_like_real_provider_name(inc)

    if cur == inc:
        return False

    if _looks_like_real_provider_name(cur) and not _looks_like_real_provider_name(inc):
        return False

    if len(inc) > len(cur) and _looks_like_real_provider_name(inc):
        return True

    if cur.lower() == inc.lower() and _looks_like_real_provider_name(inc):
        return True

    return False


def _get_unknown_provider(db: Session) -> Provider:
    stmt = select(Provider).where(Provider.provider_key == _UNKNOWN_PROVIDER_KEY)
    p = db.execute(stmt).scalar_one_or_none()
    if p is None:
        p = Provider(
            provider_key=_UNKNOWN_PROVIDER_KEY,
            display_name=_UNKNOWN_PROVIDER_KEY,
            cachet_component_id=None,
            is_active=True,
        )
        db.add(p)
        db.flush()
        return p

    # unknown-provider should always stay active
    if p.is_active is not True:
        p.is_active = True
    if not p.display_name:
        p.display_name = _UNKNOWN_PROVIDER_KEY
    return p


def get_or_create_provider(
    db: Session,
    *,
    provider_key: str,
    display_name: str,
    cachet_component_id: Optional[int] = None,
) -> Provider:
    # HARD BLOCK:
    # Never allow subject/title derived "cs123456:" pseudo-providers to be created OR returned OR reactivated.
    raw_pk = str(provider_key or "").strip()
    raw_dn = str(display_name or "").strip()

    # First-pass block on raw inputs (fast fail)
    if _looks_like_cs_provider_key(raw_pk) or _looks_like_cs_provider_key(raw_dn):
        return _get_unknown_provider(db)

    provider_key_norm = _normalize_provider_key(raw_pk)
    display_name_clean = _normalize_name_for_compare(raw_dn) or provider_key_norm

    # Second-pass block on normalized key (covers weird spacing/case)
    if _looks_like_cs_provider_key(provider_key_norm):
        return _get_unknown_provider(db)

    stmt = select(Provider).where(Provider.provider_key == provider_key_norm)
    p = db.execute(stmt).scalar_one_or_none()

    if p is None:
        p = Provider(
            provider_key=provider_key_norm,
            display_name=display_name_clean,
            cachet_component_id=cachet_component_id,
            is_active=True,
        )
        db.add(p)
        db.flush()
        return p

    # CRITICAL: If an existing DB row is cs-like, never return it (prevents reactivation loop).
    if _looks_like_cs_provider_key(p.provider_key or ""):
        # Also proactively deactivate to keep it out of list_providers()
        if p.is_active is True:
            p.is_active = False
        return _get_unknown_provider(db)

    if _should_update_display_name(p.display_name or "", display_name_clean):
        p.display_name = display_name_clean

    if cachet_component_id is not None and p.cachet_component_id != cachet_component_id:
        p.cachet_component_id = cachet_component_id

    if p.is_active is not True:
        p.is_active = True

    return p


# -------------------------
# Provider alias resolution
# -------------------------

def _normalize_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(_clean_text(value)).strip().lower()
    return s or None


def _email_domain(addr: Optional[str]) -> Optional[str]:
    a = _normalize_email(addr)
    if not a or "@" not in a:
        return None
    return a.split("@", 1)[1].strip().lower() or None


def resolve_provider_for_outlook(
    db: Session,
    *,
    provider_key_fallback: str,
    display_name_fallback: str,
    from_address: Optional[str],
    subject: Optional[str] = None,
) -> Tuple[Provider, Optional[str], Optional[str], Optional[int]]:
    """
    Resolve provider for Outlook ingestion.

    Order:
      1) provider_aliases exact match_type='from_address'
      2) provider_aliases match_type='from_domain'
      3) fallback to provider_key/display_name passed from parser

    HARDENING:
      - If fallback OR subject looks like cs-like, force unknown-provider.
      - If alias references a missing provider row, deactivate alias and fall back safely.
    """
    fa = _normalize_email(from_address)
    dom = _email_domain(fa)

    def _provider_by_id(pid: int) -> Optional[Provider]:
        return db.execute(select(Provider).where(Provider.id == pid)).scalar_one_or_none()

    # 1) Exact sender
    if fa:
        stmt = (
            select(ProviderAlias)
            .where(
                and_(
                    ProviderAlias.is_active == True,  # noqa: E712
                    ProviderAlias.match_type == "from_address",
                    ProviderAlias.match_value == fa,
                )
            )
            .order_by(ProviderAlias.priority.asc(), ProviderAlias.updated_at_utc.desc())
            .limit(1)
        )
        pa = db.execute(stmt).scalar_one_or_none()
        if pa is not None:
            p = _provider_by_id(pa.provider_id)
            if p is not None:
                # Never allow cs-like providers to be returned via alias
                if _looks_like_cs_provider_key(p.provider_key or ""):
                    return _get_unknown_provider(db), "from_address", fa, 95
                return p, "from_address", fa, 95

            # Alias is stale -> deactivate and fall through
            pa.is_active = False

    # 2) Domain fallback
    if dom:
        stmt = (
            select(ProviderAlias)
            .where(
                and_(
                    ProviderAlias.is_active == True,  # noqa: E712
                    ProviderAlias.match_type == "from_domain",
                    ProviderAlias.match_value == dom,
                )
            )
            .order_by(ProviderAlias.priority.asc(), ProviderAlias.updated_at_utc.desc())
            .limit(1)
        )
        pa = db.execute(stmt).scalar_one_or_none()
        if pa is not None:
            p = _provider_by_id(pa.provider_id)
            if p is not None:
                if _looks_like_cs_provider_key(p.provider_key or ""):
                    return _get_unknown_provider(db), "from_domain", dom, 85
                return p, "from_domain", dom, 85

            # Alias is stale -> deactivate and fall through
            pa.is_active = False

    # 3) Fallback (hardened)
    pk = str(provider_key_fallback or "").strip()
    dn = str(display_name_fallback or "").strip()
    sj = str(subject or "").strip()

    if _looks_like_cs_provider_key(pk) or _looks_like_cs_provider_key(dn) or _looks_like_cs_provider_key(sj):
        p = _get_unknown_provider(db)
        return p, None, None, None

    p = get_or_create_provider(
        db,
        provider_key=pk,
        display_name=dn or pk,
        cachet_component_id=None,
    )
    return p, None, None, None


def upsert_incident(
    db: Session,
    *,
    provider: Provider,
    source: str,
    external_id: str,
    state: str,
    status_name: Optional[str],
    message: Optional[str],
    postal_codes: Optional[List[str]],
    created_at_utc: Optional[datetime],
    updated_at_utc: Optional[datetime],
    location_source: Optional[str] = None,
    # audit/traceability fields (optional)
    from_address: Optional[str] = None,
    subject: Optional[str] = None,
    provider_match_type: Optional[str] = None,
    provider_match_value: Optional[str] = None,
    provider_confidence: Optional[int] = None,
) -> Incident:
    source_norm = (source or "").strip().lower()
    external_id_norm = str(external_id).strip()

    state_norm = (state or "active").strip().lower()
    if state_norm not in {"active", "resolved"}:
        state_norm = "active"

    status_name_clean = None
    if status_name is not None:
        s = str(_clean_text(status_name)).strip()
        status_name_clean = s or None

    message_clean = None
    if message is not None:
        m = str(_clean_text(message)).strip()
        message_clean = m or None

    from_address_clean = _normalize_email(from_address)

    subject_clean = None
    if subject is not None:
        sj = str(_clean_text(subject)).strip()
        subject_clean = sj or None

    provider_match_type_clean = None
    if provider_match_type is not None:
        mt = str(_clean_text(provider_match_type)).strip()
        provider_match_type_clean = mt or None

    provider_match_value_clean = None
    if provider_match_value is not None:
        mv = str(_clean_text(provider_match_value)).strip().lower()
        provider_match_value_clean = mv or None

    provider_confidence_clean = None
    if provider_confidence is not None:
        try:
            provider_confidence_clean = int(provider_confidence)
        except Exception:
            provider_confidence_clean = None

    stmt = select(Incident).where(and_(Incident.source == source_norm, Incident.external_id == external_id_norm))
    inc = db.execute(stmt).scalar_one_or_none()

    cleaned_codes: List[str] = []
    seen = set()
    for c in (postal_codes or []):
        c_clean = str(_clean_text(c or "")).strip()
        canonical = _format_swedish_postal_code(c_clean)
        if not canonical:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        cleaned_codes.append(canonical)

    postal_csv = ",".join(cleaned_codes) or None

    if inc is None:
        inc = Incident(
            provider_id=provider.id,
            source=source_norm,
            external_id=external_id_norm,
            state=state_norm,
            status_name=status_name_clean,
            message=message_clean,
            affected_postal_codes=postal_csv,
            created_at_utc=_utc(created_at_utc),
            updated_at_utc=_utc(updated_at_utc),
        )

        inc.from_address = from_address_clean
        inc.subject = subject_clean
        inc.provider_match_type = provider_match_type_clean
        inc.provider_match_value = provider_match_value_clean
        inc.provider_confidence = provider_confidence_clean

        db.add(inc)
        db.flush()
    else:
        inc.provider_id = provider.id

        inc.state = state_norm
        inc.status_name = status_name_clean
        inc.message = message_clean
        inc.affected_postal_codes = postal_csv
        inc.updated_at_utc = _utc(updated_at_utc)
        if inc.created_at_utc is None:
            inc.created_at_utc = _utc(created_at_utc)

        if from_address_clean is not None:
            inc.from_address = from_address_clean
        if subject_clean is not None:
            inc.subject = subject_clean

        if provider_match_type_clean is not None:
            inc.provider_match_type = provider_match_type_clean
        if provider_match_value_clean is not None:
            inc.provider_match_value = provider_match_value_clean
        if provider_confidence_clean is not None:
            inc.provider_confidence = provider_confidence_clean

    # ✅ CRITICAL FIX:
    # Always delete old locations, even when incoming postal codes are empty.
    db.execute(IncidentLocation.__table__.delete().where(IncidentLocation.incident_id == inc.id))

    # Reinsert only if we have cleaned codes
    if cleaned_codes:
        now_utc = datetime.now(timezone.utc)
        for code in cleaned_codes:
            db.add(
                IncidentLocation(
                    incident_id=inc.id,
                    postal_code=code,
                    city=None,
                    country="Sweden",
                    confidence=None,
                    source=location_source or source_norm,
                    created_at_utc=now_utc,
                )
            )
        db.flush()

    return inc


def list_providers(db: Session) -> List[Provider]:
    """
    Return active providers excluding:
      - cs-like pseudo providers
      - unknown-provider (kept for ingestion/audit but should not be shown in UI)
    """
    stmt = (
        select(Provider)
        .where(Provider.is_active == True)  # noqa: E712
        .order_by(Provider.display_name.asc())
    )
    providers = list(db.execute(stmt).scalars().all())

    out: List[Provider] = []
    for p in providers:
        pk = (p.provider_key or "").strip().lower()
        if _looks_like_cs_provider_key(pk):
            continue
        if pk == _UNKNOWN_PROVIDER_KEY:
            continue
        out.append(p)

    return out


def list_active_incidents_for_provider(db: Session, provider_id: int) -> List[Incident]:
    stmt = (
        select(Incident)
        .options(selectinload(Incident.locations))
        .where(and_(Incident.provider_id == provider_id, Incident.state == "active"))
        .order_by(desc(Incident.updated_at_utc))
    )
    return list(db.execute(stmt).scalars().all())


def get_provider_with_incidents(db: Session, provider_id: int) -> Optional[Provider]:
    stmt = (
        select(Provider)
        .options(selectinload(Provider.incidents).selectinload(Incident.locations))
        .where(Provider.id == provider_id)
    )
    p = db.execute(stmt).scalar_one_or_none()
    if p is None:
        return None

    pk = (p.provider_key or "").strip().lower()

    # If someone requests a cs-like provider_id directly, treat as not found
    if _looks_like_cs_provider_key(pk):
        return None

    # Also hide unknown-provider from UI access
    if pk == _UNKNOWN_PROVIDER_KEY:
        return None

    return p


def search_active_incidents_by_postal_code(db: Session, postal_code: str) -> List[Incident]:
    digits = _postal_digits(postal_code)
    if len(digits) != 5:
        return []

    canonical = f"{digits[:3]} {digits[3:]}"

    stmt = (
        select(Incident)
        .join(IncidentLocation, IncidentLocation.incident_id == Incident.id)
        .options(selectinload(Incident.locations))
        .where(
            and_(
                Incident.state == "active",
                or_(
                    IncidentLocation.postal_code == canonical,
                    func.regexp_replace(IncidentLocation.postal_code, r"\D", "", "g") == digits,
                ),
            )
        )
        .order_by(desc(Incident.updated_at_utc))
    )
    return list(db.execute(stmt).scalars().all())