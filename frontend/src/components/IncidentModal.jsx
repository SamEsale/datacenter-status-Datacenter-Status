// frontend/src/components/IncidentModal.jsx

import { useMemo } from "react";
import "./IncidentModal.css";

function safeLocalDateString(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

function uniqueSorted(arr) {
  return Array.from(new Set((arr ?? []).filter(Boolean))).sort();
}

function statusToAccentClass(statusText) {
  const s = String(statusText ?? "").toLowerCase().trim();

  if (s === "down" || s === "större avbrott" || s === "major outage" || s === "major outages") {
    return "modal-accent--down";
  }
  if (
    s === "waiting" ||
    s === "mindre avbrott" ||
    s === "minor outage" ||
    s === "minor interruption" ||
    s === "minor interrupt"
  ) {
    return "modal-accent--waiting";
  }
  if (s === "prestandaproblem" || s === "performance issue" || s === "performance issues") {
    return "modal-accent--performance";
  }
  if (s === "resolved") {
    return "modal-accent--up";
  }
  // Default
  return "modal-accent--up";
}

export default function IncidentModal({ open, isOpen, providerStatus, onClose }) {
  const modalOpen = typeof isOpen === "boolean" ? isOpen : !!open;
  if (!modalOpen) return null;

  const providerName = providerStatus?.provider ?? providerStatus?.display_name ?? "Unknown";
  const status = providerStatus?.status ?? providerStatus?.status_name ?? "—";

  const activeCount =
    providerStatus?.active_incident_count ??
    (Array.isArray(providerStatus?.active_incidents) ? providerStatus.active_incidents.length : 0);

  const latestUpdate = safeLocalDateString(providerStatus?.latest_active_update_at_utc);

  const accentClass = statusToAccentClass(status);

  // Combine incidents: active first, then recent. Deduplicate by id if repeated.
  const incidents = useMemo(() => {
    const active = Array.isArray(providerStatus?.active_incidents)
      ? providerStatus.active_incidents
      : [];
    const recent = Array.isArray(providerStatus?.recent_incidents)
      ? providerStatus.recent_incidents
      : [];

    const map = new Map();
    [...active, ...recent].forEach((inc) => {
      const id = inc?.id ?? inc?.external_id;
      if (!id) return;
      map.set(String(id), inc);
    });

    // Sort newest updated/created first
    return Array.from(map.values()).sort((a, b) => {
      const ta = new Date(a?.updated_at_utc ?? a?.created_at_utc ?? 0).getTime();
      const tb = new Date(b?.updated_at_utc ?? b?.created_at_utc ?? 0).getTime();
      return tb - ta;
    });
  }, [providerStatus]);

  const aggregatedIncidentIds = useMemo(() => {
    return incidents.map((h) => h?.id ?? h?.external_id).filter(Boolean);
  }, [incidents]);

  // ✅ Requirement #1: No duplicates in aggregated postal codes
  const aggregatedPostalCodes = useMemo(() => {
    const pcs = [];
    incidents.forEach((h) => (h?.postal_codes ?? []).forEach((p) => pcs.push(p)));
    return uniqueSorted(pcs);
  }, [incidents]);

  // Close on overlay click (but not when clicking inside the modal card)
  const onOverlayClick = (e) => {
    if (e.target === e.currentTarget) onClose?.();
  };

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Incident details"
      onClick={onOverlayClick}
    >
      <div className={`modal-card ${accentClass}`}>
        <div className="modal-header">
          <div>
            <h2 className="modal-title">{providerName}</h2>
            <p className="modal-subtitle">
              <strong>Status:</strong> {status} · <strong>Active incidents:</strong> {activeCount}
              {" · "}
              <strong>Latest update:</strong> {latestUpdate}
            </p>
          </div>

          <button className="modal-close" type="button" onClick={onClose} aria-label="Close modal">
            ×
          </button>
        </div>

        <div className="modal-body">
          {/* ✅ Requirement #5: Provider summary visually separated */}
          <div className="summary-block">
            <div className="summary-grid">
              <div className="summary-item">
                <div className="summary-label">Provider</div>
                <div className="summary-value">{providerName}</div>
              </div>

              <div className="summary-item">
                <div className="summary-label">Status</div>
                <div className="summary-value">{status}</div>
              </div>

              <div className="summary-item summary-item--wide">
                <div className="summary-label">Incident ID(s)</div>
                <div className="summary-value">
                  {aggregatedIncidentIds.length ? aggregatedIncidentIds.join(", ") : "—"}
                </div>
              </div>
            </div>

            <div className="summary-label" style={{ marginTop: 10 }}>
              Affected postal codes
            </div>

            {/* ✅ Requirement #3: Chips */}
            <div className="postal-chip-container">
              {aggregatedPostalCodes.length ? (
                aggregatedPostalCodes.map((pc) => (
                  <span key={pc} className="postal-chip">
                    {pc}
                  </span>
                ))
              ) : (
                <span className="muted">—</span>
              )}
            </div>
          </div>

          {/* Incidents list */}
          {!incidents.length ? (
            <div className="modal-empty">No incidents found for this provider.</div>
          ) : (
            incidents.map((inc) => {
              const id = inc?.id ?? inc?.external_id ?? "—";
              const created = safeLocalDateString(inc?.created_at_utc);
              const updated = safeLocalDateString(inc?.updated_at_utc);

              // ✅ Requirement #1: No duplicates in per-incident postal codes
              const pcs = uniqueSorted(inc?.postal_codes ?? []);
              const message = inc?.message ?? "—";

              // Optional fields from Outlook pipeline (if present)
              const subject = inc?.subject;
              const fromName = inc?.from_name;
              const fromAddress = inc?.from_address;
              const state = inc?.state;
              const statusName = inc?.status_name; // ✅ Requirement #2: keep status name

              return (
                <div key={String(id)} className="incident-block">
                  <p className="incident-id">
                    <strong>Incident ID:</strong> {id}
                  </p>

                  <p className="incident-time">
                    <strong>Created:</strong> {created} {" · "}
                    <strong>Updated:</strong> {updated}
                  </p>

                  {typeof state === "string" && state.trim() !== "" ? (
                    <p className="incident-status">
                      <strong>State:</strong> {state}
                    </p>
                  ) : null}

                  {typeof statusName === "string" && statusName.trim() !== "" ? (
                    <p className="incident-status">
                      <strong>Status name:</strong> {statusName}
                    </p>
                  ) : null}

                  {typeof subject === "string" && subject.trim() !== "" ? (
                    <p className="incident-name">
                      <strong>Subject:</strong> {subject}
                    </p>
                  ) : null}

                  {typeof fromName === "string" && fromName.trim() !== "" ? (
                    <p className="incident-name">
                      <strong>From name:</strong> {fromName}
                    </p>
                  ) : null}

                  {typeof fromAddress === "string" && fromAddress.trim() !== "" ? (
                    <p className="incident-name">
                      <strong>From address:</strong> {fromAddress}
                    </p>
                  ) : null}

                  <p className="incident-status">
                    <strong>Postal codes:</strong>
                  </p>

                  {/* ✅ Requirement #3: Chips */}
                  <div className="postal-chip-container">
                    {pcs.length ? (
                      pcs.map((pc) => (
                        <span key={pc} className="postal-chip">
                          {pc}
                        </span>
                      ))
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </div>

                  <div className="incident-message-box">
                    <div className="incident-message-label">Message</div>

                    {/* ✅ Requirement #4: long message collapsed (scrollable box) */}
                    <pre className="incident-message-pre">{message}</pre>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}