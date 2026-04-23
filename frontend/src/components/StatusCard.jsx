// frontend/src/components/StatusCard.jsx

import UpIcon from "../assets/Up.png";
import DownIcon from "../assets/Down.png";
import IncidentIcon from "../assets/Incident.png";
import WaitingIcon from "../assets/Waiting.png";
import "./StatusCard.css";

function safeLocalDateString(value) {
  if (!value) return null;
  try {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleString();
  } catch {
    return null;
  }
}

// Removes a trailing " (123)" if it exists, otherwise returns the original string.
function stripTrailingCount(statusText) {
  return String(statusText ?? "").replace(/\s*\(\s*\d+\s*\)\s*$/, "");
}

// ✅ Hide "unknown" providers instead of rendering a card
function shouldHideProvider(provider) {
  const s = typeof provider === "string" ? provider.trim() : "";
  if (!s) return true;

  const n = s.toLowerCase();

  // Hide the common variants
  if (n === "unknown") return true;
  if (n === "unknown provider") return true;
  if (n === "unknown-provider") return true;

  return false;
}

export default function StatusCard({ incident, onClick }) {
  const {
    provider,
    status,
    subject,
    active_incident_count,
    latest_active_update_at_utc,
  } = incident || {};

  // ✅ IMPORTANT: stop rendering these cards entirely
  if (shouldHideProvider(provider)) return null;

  // Defensive: ensure status does not include "(number)" from backend strings
  const cleanedStatus = stripTrailingCount(status ?? "Waiting");

  const normalized = String(cleanedStatus ?? "").toLowerCase().trim();

  // Backend currently uses: "Down" | "Fungerar"
  // Keep legacy mappings too, so you don’t break if you reintroduce Cachet-like strings later.
  const isPerformance = ["prestandaproblem", "performance issues", "performance issue"].includes(
    normalized
  );

  const isWaiting = [
    "waiting",
    "mindre avbrott",
    "minor interrupt",
    "minor interruption",
    "minor outage",
  ].includes(normalized);

  const isDown = ["down", "större avbrott", "major outages", "major outage"].includes(normalized);

  // If backend says there are active incidents, treat as Down even if status text changes.
  const hasActiveIncidents =
    typeof active_incident_count === "number" ? active_incident_count > 0 : false;

  const effectiveIsDown = hasActiveIncidents || isDown;

  // Exactly one class (prevents multiple backgrounds fighting each other)
  const stateClass = effectiveIsDown
    ? "down"
    : isWaiting
      ? "waiting"
      : isPerformance
        ? "performance"
        : "up";

  // Icon mapping (as requested)
  // Up => Up.png
  // Down => Down.png
  // Performance issues => Incident.png
  // Minor outage => Waiting.png
  const icon = effectiveIsDown
    ? DownIcon
    : isPerformance
      ? IncidentIcon
      : isWaiting
        ? WaitingIcon
        : UpIcon;

  const classes = ["status-card", "status-card-clickable", stateClass].join(" ");

  const updatedLabel = safeLocalDateString(latest_active_update_at_utc);

  // provider is guaranteed non-empty here because of shouldHideProvider()
  const providerLabel = String(provider).trim();

  return (
    <button
      type="button"
      className={classes}
      onClick={onClick}
      aria-label={`Open incident details for ${providerLabel}`}
    >
      <div className="status-header">
        <img src={icon} alt="status" className="status-icon" />
        <h2 className="status-title">{providerLabel}</h2>
      </div>

      <div className="status-details">
        <p>
          <strong>Status:</strong> {cleanedStatus}
        </p>

        {updatedLabel ? (
          <p className="incident-message">
            <strong>Updated:</strong> {updatedLabel}
          </p>
        ) : null}

        {subject && String(subject).trim() !== "" ? (
          <p className="incident-message">
            <strong>Latest Message:</strong> {String(subject)}
          </p>
        ) : null}
      </div>
    </button>
  );
}