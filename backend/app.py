from __future__ import annotations

import os
import time
import asyncio
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from threading import Lock
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text  # ✅ SQLAlchemy 2.x safe textual SQL

from .db import get_session

from .repository import (
    get_or_create_provider,
    resolve_provider_for_outlook,
    upsert_incident,
    list_providers,
    get_provider_with_incidents,
    search_active_incidents_by_postal_code,
)

from .services_outlook import fetch_recent_outlook_incidents
from .services_cachet import (
    fetch_components,
    fetch_recent_incidents,
    map_cachet_incident_to_record,
    normalize_provider_key,
)

# -----------------------------------------------------------------------------
# Logging (Render-safe)
# -----------------------------------------------------------------------------
_LOG_LEVEL_NAME = (os.getenv("LOG_LEVEL", "INFO") or "INFO").strip().upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)

logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("datacenter-status")

# -----------------------------------------------------------------------------
# Environment loading (LOCAL-ONLY, production safe)
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"


def _is_truthy_env(name: str) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _should_load_dotenv() -> bool:
    if _is_truthy_env("RENDER"):
        return False

    env = (os.getenv("ENV", "") or "").strip().lower()
    environment = (os.getenv("ENVIRONMENT", "") or "").strip().lower()

    if env in {"prod", "production"} or environment in {"prod", "production"}:
        return False

    return ENV_PATH.exists()


if _should_load_dotenv():
    load_dotenv(dotenv_path=ENV_PATH, override=False)

# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------
CACHE_REFRESH_SECONDS = int(os.getenv("CACHE_REFRESH_SECONDS", "60"))

CACHET_ENABLED = os.getenv("CACHET_ENABLED", "1").strip().lower() in {"1", "true", "yes"}

CACHET_INGEST_INCIDENTS = os.getenv("CACHET_INGEST_INCIDENTS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
}
CACHET_CAN_RENAME_PROVIDERS = os.getenv("CACHET_CAN_RENAME_PROVIDERS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
}

_cache_lock = Lock()
_cache_payload: Optional[Dict[str, Any]] = None
_cache_last_ok_ts: Optional[float] = None
_cache_last_attempt_ts: Optional[float] = None
_cache_last_error: Optional[str] = None

# -----------------------------------------------------------------------------
# Outlook fetch telemetry (debug + determinism)
# -----------------------------------------------------------------------------
_outlook_last_ok_ts: Optional[float] = None
_outlook_last_attempt_ts: Optional[float] = None
_outlook_last_error: Optional[str] = None
_outlook_last_count: int = 0

_app_start_ts = time.time()

_UNKNOWNISH = {
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


def _is_unknownish_text(value: Any) -> bool:
    s = str(value or "").strip().lower()
    s = " ".join(s.split())
    if not s:
        return True
    if s in _UNKNOWNISH:
        return True
    if s.startswith("unknown"):
        return True
    return False


def _is_unknown_provider_row(provider_obj: Any) -> bool:
    pk = getattr(provider_obj, "provider_key", None)
    dn = getattr(provider_obj, "display_name", None)
    if _is_unknownish_text(pk) or _is_unknownish_text(dn):
        return True
    return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _set_cache_error(err: str) -> None:
    global _cache_last_error
    with _cache_lock:
        _cache_last_error = err


def _write_cache(payload: Dict[str, Any]) -> None:
    global _cache_payload, _cache_last_ok_ts, _cache_last_error
    with _cache_lock:
        _cache_payload = payload
        _cache_last_ok_ts = time.time()
        _cache_last_error = None


def _read_cache_snapshot() -> Optional[Dict[str, Any]]:
    """
    Return a cached payload snapshot and attach cache meta.
    IMPORTANT: Merge into existing payload["meta"] instead of overwriting it.
    """
    with _cache_lock:
        if _cache_payload is None:
            return None

        payload = dict(_cache_payload)

        existing_meta: Dict[str, Any] = {}
        if isinstance(payload.get("meta"), dict):
            existing_meta = dict(payload["meta"])

        cache_meta = {
            "ok": True if _cache_last_error is None else False,
            "cache_refresh_seconds": CACHE_REFRESH_SECONDS,
            "last_ok_ts": _cache_last_ok_ts,
            "last_attempt_ts": _cache_last_attempt_ts,
            "last_error": _cache_last_error,
        }

        payload["meta"] = {**existing_meta, **cache_meta}
        return payload


# -----------------------------------------------------------------------------
# Swedish postal code normalization + display formatting
# -----------------------------------------------------------------------------
def _postal_digits(value: Any) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(c for c in str(value) if c.isdigit())
    if len(digits) != 5:
        return None
    return digits


def _postal_spaced(digits5: str) -> str:
    return f"{digits5[:3]} {digits5[3:]}"


# -----------------------------------------------------------------------------
# Version helpers
# -----------------------------------------------------------------------------
def _best_effort_git_sha() -> Optional[str]:
    """
    Best-effort commit sha from common CI/runtime environment variables.
    We do NOT assume a single provider-specific env var exists.
    """
    keys = [
        "RENDER_GIT_COMMIT",
        "GIT_COMMIT",
        "COMMIT_SHA",
        "SOURCE_VERSION",
        "VERCEL_GIT_COMMIT_SHA",
        "GITHUB_SHA",
    ]
    for k in keys:
        v = (os.getenv(k, "") or "").strip()
        if v:
            return v
    return None


# -----------------------------------------------------------------------------
# Ingestion
# -----------------------------------------------------------------------------
def ingest_sources_into_db() -> None:
    db = get_session()
    try:
        # ---------------------------------------------------------------------
        # 1) OUTLOOK FIRST (PRIMARY)
        # ---------------------------------------------------------------------
        global _outlook_last_ok_ts, _outlook_last_attempt_ts, _outlook_last_error, _outlook_last_count

        _outlook_last_attempt_ts = time.time()
        try:
            outlook = fetch_recent_outlook_incidents()
            _outlook_last_ok_ts = time.time()
            _outlook_last_error = None
            _outlook_last_count = len(outlook or [])
        except RuntimeError as e:
            _outlook_last_error = f"RuntimeError: {e}"
            _outlook_last_count = 0
            logger.warning("Skipping Outlook ingest (runtime): %s", e)
            outlook = []
        except Exception as e:
            _outlook_last_error = f"{type(e).__name__}: {e}"
            _outlook_last_count = 0
            logger.exception("Outlook ingest failed")
            outlook = []

        for rec in outlook:
            external_id = rec.get("external_id")
            if not external_id:
                logger.warning(
                    "Outlook record missing external_id; skipping. subject=%r from=%r",
                    rec.get("subject"),
                    rec.get("from_address"),
                )
                continue

            provider_key_fallback = str(rec.get("provider_key") or "unknown-provider").strip() or "unknown-provider"
            display_name_fallback = (
                str(rec.get("display_name") or provider_key_fallback).strip() or provider_key_fallback
            )

            from_address = rec.get("from_address")
            subject = rec.get("subject")

            provider, match_type, match_value, confidence = resolve_provider_for_outlook(
                db,
                provider_key_fallback=provider_key_fallback,
                display_name_fallback=display_name_fallback,
                from_address=from_address,
                subject=subject,
            )

            postal_codes_raw = rec.get("postal_codes") or []
            postal_codes_digits: List[str] = []
            for pc in postal_codes_raw:
                d = _postal_digits(pc)
                if d:
                    postal_codes_digits.append(d)

            upsert_incident(
                db,
                provider=provider,
                source="outlook",
                external_id=str(external_id),
                state=(rec.get("state") or "active"),
                status_name=rec.get("status_name"),
                message=rec.get("message"),
                postal_codes=postal_codes_digits,
                created_at_utc=rec.get("created_at_utc"),
                updated_at_utc=rec.get("updated_at_utc"),
                location_source="outlook",
                from_address=from_address,
                subject=subject,
                provider_match_type=match_type,
                provider_match_value=match_value,
                provider_confidence=confidence,
            )

        # ---------------------------------------------------------------------
        # 2) CACHET SECONDARY (OPTIONAL)
        # ---------------------------------------------------------------------
        if CACHET_ENABLED:
            try:
                components = fetch_components()
            except RuntimeError as e:
                logger.warning("Skipping Cachet components fetch (runtime): %s", e)
                components = []
            except Exception:
                logger.exception("Cachet components fetch failed")
                components = []

            comp_by_id: Dict[int, Dict[str, Any]] = {}
            for c in components:
                try:
                    cid = int(c.get("id"))
                except Exception:
                    continue

                comp_by_id[cid] = c
                name = str(c.get("name") or "").strip()
                provider_key = normalize_provider_key(name)
                if not provider_key:
                    continue

                display_name_for_cachet = name if CACHET_CAN_RENAME_PROVIDERS else provider_key

                get_or_create_provider(
                    db,
                    provider_key=provider_key,
                    display_name=display_name_for_cachet,
                    cachet_component_id=cid,
                )

            if CACHET_INGEST_INCIDENTS:
                try:
                    incidents = fetch_recent_incidents(per_page=200)
                except RuntimeError as e:
                    logger.warning("Skipping Cachet incidents fetch (runtime): %s", e)
                    incidents = []
                except Exception:
                    logger.exception("Cachet incidents fetch failed")
                    incidents = []

                for inc in incidents:
                    rec = map_cachet_incident_to_record(inc)
                    if not rec:
                        continue

                    if not rec.get("external_id"):
                        continue

                    cid = rec.get("cachet_component_id")
                    if isinstance(cid, int) and cid in comp_by_id:
                        cname = str(comp_by_id[cid].get("name") or "").strip()
                        if cname:
                            rec["display_name"] = cname
                            rec["provider_key"] = normalize_provider_key(cname)

                    if not CACHET_CAN_RENAME_PROVIDERS:
                        rec["display_name"] = rec["provider_key"]

                    p = get_or_create_provider(
                        db,
                        provider_key=rec["provider_key"],
                        display_name=rec["display_name"],
                        cachet_component_id=rec.get("cachet_component_id"),
                    )

                    postal_codes_raw = rec.get("postal_codes") or []
                    postal_codes_digits: List[str] = []
                    for pc in postal_codes_raw:
                        d = _postal_digits(pc)
                        if d:
                            postal_codes_digits.append(d)

                    upsert_incident(
                        db,
                        provider=p,
                        source="cachet",
                        external_id=rec["external_id"],
                        state=(rec.get("state") or "active"),
                        status_name=rec.get("status_name"),
                        message=rec.get("message"),
                        postal_codes=postal_codes_digits,
                        created_at_utc=rec.get("created_at_utc"),
                        updated_at_utc=rec.get("updated_at_utc"),
                        location_source="cachet",
                    )

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# -----------------------------------------------------------------------------
# Output shaping helpers
# -----------------------------------------------------------------------------
def _incident_postal_codes_display(inc) -> List[str]:
    try:
        locs = getattr(inc, "locations", None) or []
        out_digits: List[str] = []
        for loc in locs:
            d = _postal_digits(getattr(loc, "postal_code", None))
            if d:
                out_digits.append(d)
        if out_digits:
            unique_digits = sorted(set(out_digits))
            return [_postal_spaced(d) for d in unique_digits]
    except Exception:
        pass

    csv = getattr(inc, "affected_postal_codes", None)
    if csv:
        out_digits = []
        for p in str(csv).split(","):
            d = _postal_digits(p)
            if d:
                out_digits.append(d)
        unique_digits = sorted(set(out_digits))
        return [_postal_spaced(d) for d in unique_digits]

    return []


def build_status_payload_from_db() -> Dict[str, Any]:
    """
    Deterministic rule per provider (NON-NEGOTIABLE):
      1) If Outlook has any ACTIVE incident -> Down
      2) Else if Outlook has ANY signal (any incident exists) -> Fungerar
      3) Else -> Cachet status label fallback (if available), else "Ok"
    """
    db = get_session()
    try:
        providers = list_providers(db)

        # Cachet component status lookup (fallback only)
        cachet_label_by_component_id: Dict[int, str] = {}
        if CACHET_ENABLED:
            try:
                comps = fetch_components()
            except Exception as e:
                logger.warning("Cachet fetch_components failed during payload build: %s", e)
                comps = []

            for c in comps or []:
                try:
                    cid = int(c.get("id"))
                except Exception:
                    continue

                label = (
                    str(c.get("status_name") or "").strip()
                    or str(c.get("status") or "").strip()
                    or "Ok"
                )
                cachet_label_by_component_id[cid] = label

        data: List[Dict[str, Any]] = []
        incidents_out: List[Dict[str, Any]] = []

        for p in providers:
            # ✅ FINAL GUARD: Never show unknown providers on the dashboard
            if _is_unknown_provider_row(p):
                continue

            # Load incidents to evaluate Outlook signal deterministically
            p_full = get_provider_with_incidents(db, int(p.id))
            all_incs = list(p_full.incidents or []) if p_full is not None else []

            all_incs_sorted = sorted(
                all_incs,
                key=lambda x: x.updated_at_utc or _utc_now(),
                reverse=True,
            )

            # Outlook signal
            outlook_incs = [x for x in all_incs_sorted if (x.source or "").lower() == "outlook"]
            outlook_active = [x for x in outlook_incs if (x.state or "").lower() == "active"]
            outlook_has_signal = len(outlook_incs) > 0

            # Active incidents across all sources (for UI incident listing)
            active_incs_all_sources = [x for x in all_incs_sorted if (x.state or "").lower() == "active"]

            # Decide status (rule above)
            if len(outlook_active) > 0:
                status = "Down"
                active_count = len(outlook_active)
                source = "outlook"
            elif outlook_has_signal:
                status = "Fungerar"
                active_count = 0
                source = "outlook"
            else:
                fallback_label = "Ok"
                cid = getattr(p, "cachet_component_id", None)
                if isinstance(cid, int) and cid in cachet_label_by_component_id:
                    fallback_label = cachet_label_by_component_id[cid]

                status = fallback_label
                active_count = 0
                source = "cachet_fallback"

            latest_active_update = None
            if len(outlook_active) > 0:
                latest_active_update = outlook_active[0].updated_at_utc
            elif len(active_incs_all_sources) > 0:
                latest_active_update = active_incs_all_sources[0].updated_at_utc

            data.append(
                {
                    "id": int(p.id),
                    "name": p.display_name,
                    "status": status,
                    "active_incident_count": active_count,
                    "latest_active_update_at_utc": latest_active_update.isoformat() if latest_active_update else None,
                    "source": source,
                    "outlook_has_signal": outlook_has_signal,
                }
            )

            # Emit active incidents (all sources) so existing UI continues to work
            for inc in active_incs_all_sources:
                incidents_out.append(
                    {
                        "id": int(inc.id),
                        "provider_id": int(inc.provider_id),
                        "source": inc.source,
                        "external_id": inc.external_id,
                        "state": inc.state,
                        "status_name": inc.status_name,
                        "message": inc.message,
                        "postal_codes": _incident_postal_codes_display(inc),
                        "created_at_utc": inc.created_at_utc.isoformat() if inc.created_at_utc else None,
                        "updated_at_utc": inc.updated_at_utc.isoformat() if inc.updated_at_utc else None,
                    }
                )

        outlook_fetch_ok = _outlook_last_error is None and (_outlook_last_ok_ts is not None)

        return {
            "data": data,
            "incidents": incidents_out,
            "meta": {
                "primary_source": "outlook",
                "outlook_fetch_ok": outlook_fetch_ok,
                "outlook_last_ok_ts": _outlook_last_ok_ts,
                "outlook_last_attempt_ts": _outlook_last_attempt_ts,
                "outlook_last_count": _outlook_last_count,
                "outlook_error": _outlook_last_error,
            },
        }
    finally:
        db.close()


async def _refresh_loop() -> None:
    global _cache_last_attempt_ts
    while True:
        _cache_last_attempt_ts = time.time()
        try:
            await asyncio.to_thread(ingest_sources_into_db)
            payload = await asyncio.to_thread(build_status_payload_from_db)
            _write_cache(payload)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.exception("Refresh loop failed: %s", err)
            _set_cache_error(err)

        await asyncio.sleep(CACHE_REFRESH_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(BaseException):
            await task


app = FastAPI(title="Datacenter Status API", lifespan=lifespan)

default_origins = [
    "https://datacenter-status-frontend.onrender.com",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
extra = os.getenv("FRONTEND_ORIGINS", "").strip()
extra_origins = [o.strip() for o in extra.split(",") if o.strip()]
allow_origins = default_origins + extra_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> Dict[str, Any]:
    return {
        "service": "Datacenter Status API",
        "status_endpoint": "/status",
        "health_endpoint": "/healthz",
        "version_endpoint": "/version",
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


@app.get("/version")
def version() -> Dict[str, Any]:
    sha = _best_effort_git_sha()
    return {
        "service": "datacenter-status",
        "git_sha": sha,
        "git_sha_short": (sha[:7] if isinstance(sha, str) and len(sha) >= 7 else None),
        "app_start_utc": datetime.fromtimestamp(_app_start_ts, tz=timezone.utc).isoformat(),
        "now_utc": _utc_now().isoformat(),
        "cache_refresh_seconds": CACHE_REFRESH_SECONDS,
    }


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    now = time.time()

    db_ok = True
    db_latency_ms = 0
    db_error = None
    t0 = time.perf_counter()
    try:
        db = get_session()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as e:
        db_ok = False
        db_error = f"{type(e).__name__}: {e}"
    finally:
        db_latency_ms = int((time.perf_counter() - t0) * 1000)

    snap = _read_cache_snapshot()
    cache_present = snap is not None
    refresh_seconds = CACHE_REFRESH_SECONDS
    last_ok_ts = None
    last_attempt_ts = None
    last_error = None

    if snap and isinstance(snap.get("meta"), dict):
        m = snap["meta"]
        last_ok_ts = m.get("last_ok_ts")
        last_attempt_ts = m.get("last_attempt_ts")
        last_error = m.get("last_error")

    age_seconds = None
    if last_ok_ts is not None:
        try:
            age_seconds = float(now - float(last_ok_ts))
        except Exception:
            age_seconds = None

    overall = "ok" if db_ok else "degraded"

    return {
        "status": overall,
        "db": {"ok": db_ok, "latency_ms": db_latency_ms, "error": db_error},
        "cache": {
            "present": cache_present,
            "refresh_seconds": refresh_seconds,
            "age_seconds": age_seconds,
            "last_ok_ts": last_ok_ts,
            "last_attempt_ts": last_attempt_ts,
            "last_error": last_error,
        },
        "uptime_seconds": float(now - _app_start_ts),
        "now_utc": _utc_now().isoformat(),
    }


@app.get("/status")
def get_status() -> Dict[str, Any]:
    snapshot = _read_cache_snapshot()
    if snapshot is not None:
        return snapshot

    global _cache_last_attempt_ts
    _cache_last_attempt_ts = time.time()

    try:
        ingest_sources_into_db()
        payload = build_status_payload_from_db()
        _write_cache(payload)
        return _read_cache_snapshot() or payload
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.exception("Inline /status refresh failed: %s", err)
        _set_cache_error(err)

        return JSONResponse(
            status_code=200,
            content={
                "data": [],
                "incidents": [],
                "meta": {
                    "ok": False,
                    "reason": "refresh_failed",
                    "cache_refresh_seconds": CACHE_REFRESH_SECONDS,
                    "last_ok_ts": _cache_last_ok_ts,
                    "last_attempt_ts": _cache_last_attempt_ts,
                    "last_error": _cache_last_error,
                },
            },
        )


@app.get("/providers/{provider_id}")
def get_provider_details(provider_id: int) -> Dict[str, Any]:
    db = get_session()
    try:
        p = get_provider_with_incidents(db, provider_id)
        if p is None:
            raise HTTPException(status_code=404, detail="provider_not_found")

        all_incs = sorted(
            list(p.incidents or []),
            key=lambda x: x.updated_at_utc or _utc_now(),
            reverse=True,
        )
        active_incs = [x for x in all_incs if (x.state or "").lower() == "active"]

        status = "Down" if len(active_incs) > 0 else "Fungerar"
        latest_active_update = active_incs[0].updated_at_utc if active_incs else None
        latest_overall_update = all_incs[0].updated_at_utc if all_incs else None

        def inc_to_dict(inc) -> Dict[str, Any]:
            return {
                "id": int(inc.id),
                "provider_id": int(inc.provider_id),
                "source": inc.source,
                "external_id": inc.external_id,
                "state": inc.state,
                "status_name": inc.status_name,
                "message": inc.message,
                "postal_codes": _incident_postal_codes_display(inc),
                "created_at_utc": inc.created_at_utc.isoformat() if inc.created_at_utc else None,
                "updated_at_utc": inc.updated_at_utc.isoformat() if inc.updated_at_utc else None,
            }

        return {
            "provider": {
                "id": int(p.id),
                "name": p.display_name,
                "status": status,
                "active_incident_count": len(active_incs),
                "latest_active_update_at_utc": latest_active_update.isoformat() if latest_active_update else None,
                "latest_overall_update_at_utc": latest_overall_update.isoformat() if latest_overall_update else None,
            },
            "active_incidents": [inc_to_dict(x) for x in active_incs],
            "recent_incidents": [inc_to_dict(x) for x in all_incs[:20]],
        }
    finally:
        db.close()


@app.get("/search/postal-code/{postal_code}")
def search_by_postal_code(postal_code: str) -> Dict[str, Any]:
    digits = _postal_digits(postal_code)
    if not digits:
        raise HTTPException(status_code=400, detail="invalid_postal_code (must be Swedish 5-digit)")

    display = _postal_spaced(digits)

    db = get_session()
    try:
        incidents = search_active_incidents_by_postal_code(db, digits)

        results: List[Dict[str, Any]] = []
        for inc in incidents:
            provider_name = None
            if getattr(inc, "provider", None) is not None:
                provider_name = inc.provider.display_name

            results.append(
                {
                    "incident_id": int(inc.id),
                    "provider_id": int(inc.provider_id),
                    "provider_name": provider_name,
                    "status_name": inc.status_name,
                    "message": inc.message,
                    "postal_codes": _incident_postal_codes_display(inc),
                    "updated_at_utc": inc.updated_at_utc.isoformat() if inc.updated_at_utc else None,
                }
            )

        return {
            "postal_code": display,
            "postal_code_digits": digits,
            "match_count": len(results),
            "results": results,
        }
    finally:
        db.close()