// frontend/src/pages/Dashboard.jsx

import { useEffect, useMemo, useRef, useState } from "react";
import StatusCard from "../components/StatusCard";
import AutoRefreshWrapper from "../components/AutoRefreshWrapper";
import IncidentModal from "../components/IncidentModal";
import "./Dashboard.css";

function fmtTime(tsMs) {
  try {
    const d = new Date(tsMs);
    if (Number.isNaN(d.getTime())) return new Date().toLocaleTimeString();
    return d.toLocaleTimeString();
  } catch {
    return new Date().toLocaleTimeString();
  }
}

function sanitizeProviderName(name) {
  const s = typeof name === "string" ? name.trim() : "";
  if (!s) return "Unknown";
  if (s.toLowerCase() === "unknown-provider") return "Unknown";
  return s;
}

function normalizePostalCode(s) {
  // keep as "NNN NN" if user types it, but normalize whitespace
  return String(s ?? "").trim().replace(/\s+/g, " ");
}

function normalizePostalNeedle(s) {
  // Make postal matching robust:
  // "586 43" => "58643"
  // "58643"  => "58643"
  return String(s ?? "").replace(/\D/g, "").trim();
}

// Robustly pull incidents from possible payload locations.
function extractIncidentsFromStatusPayload(json) {
  const candidates = [
    json?.incidents,
    json?.data_incidents,
    json?.outlook_incidents,
    json?.meta?.incidents,
  ];

  for (const c of candidates) {
    if (Array.isArray(c)) return c;
  }

  // Sometimes APIs return a list of incidents under data
  if (Array.isArray(json?.data) && json.data.length) {
    const first = json.data[0];
    const looksLikeIncident =
      first &&
      typeof first === "object" &&
      ("provider_id" in first) &&
      ("external_id" in first || "message" in first || "postal_codes" in first);
    if (looksLikeIncident) return json.data;
  }

  return [];
}

export default function Dashboard() {
  const [providers, setProviders] = useState([]);
  const [timestampMs, setTimestampMs] = useState(Date.now());
  const [selected, setSelected] = useState(null);

  const [postalFilter, setPostalFilter] = useState("");
  const [cacheRefreshSeconds, setCacheRefreshSeconds] = useState(120);

  const apiBase = import.meta.env.VITE_API_BASE_URL;
  const formattedTime = useMemo(() => fmtTime(timestampMs), [timestampMs]);

  const inFlightRef = useRef(false);

  const loadStatus = async () => {
    try {
      if (!apiBase) return;
      if (inFlightRef.current) return;

      inFlightRef.current = true;

      const res = await fetch(`${apiBase}/status`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const json = await res.json();

      const crs = json?.meta?.cache_refresh_seconds;
      if (typeof crs === "number" && Number.isFinite(crs) && crs > 0) {
        setCacheRefreshSeconds(crs);
      }

      const rows = Array.isArray(json?.data) ? json.data : [];

      // Flat incident objects (if present)
      const allIncidents = extractIncidentsFromStatusPayload(json);

      // Group incidents by provider_id
      const incidentsByProviderId = new Map();
      for (const inc of allIncidents) {
        const pid = inc?.provider_id;
        if (pid == null) continue;
        const key = String(pid);
        if (!incidentsByProviderId.has(key)) incidentsByProviderId.set(key, []);
        incidentsByProviderId.get(key).push(inc);
      }

      const mapped = rows.map((item) => {
        const providerId = item?.id ?? null;

        // 1) Prefer nested incidents if backend provides them on the provider row
        const nestedActive = Array.isArray(item?.active_incidents) ? item.active_incidents : null;
        const nestedRecent = Array.isArray(item?.recent_incidents) ? item.recent_incidents : null;

        // 2) Otherwise attach incidents from the flat list grouped by provider_id
        const grouped = providerId != null ? incidentsByProviderId.get(String(providerId)) : null;
        const groupedList = Array.isArray(grouped) ? grouped : [];

        const activeFromGrouped = groupedList.filter(
          (x) => String(x?.state ?? "").toLowerCase() === "active"
        );
        const recentFromGrouped = groupedList.filter(
          (x) => String(x?.state ?? "").toLowerCase() !== "active"
        );

        const active_incidents = nestedActive ?? activeFromGrouped;
        const recent_incidents = nestedRecent ?? recentFromGrouped;

        const activeCountFromArrays = Array.isArray(active_incidents) ? active_incidents.length : 0;

        const active_incident_count =
          typeof item?.active_incident_count === "number"
            ? item.active_incident_count
            : activeCountFromArrays;

        return {
          provider_id: providerId,
          provider: sanitizeProviderName(item?.name),
          status: item?.status ?? "Waiting",
          active_incident_count,
          latest_active_update_at_utc: item?.latest_active_update_at_utc ?? null,
          subject: item?.subject ?? "",

          active_incidents,
          recent_incidents,
        };
      });

      setProviders(mapped);

      // Keep selected in sync after refresh
      if (selected?.provider_id != null) {
        const updatedSelected = mapped.find((p) => p.provider_id === selected.provider_id);
        if (updatedSelected) setSelected(updatedSelected);
      }

      const lastOkTsSeconds = json?.meta?.last_ok_ts;
      if (typeof lastOkTsSeconds === "number" && Number.isFinite(lastOkTsSeconds)) {
        setTimestampMs(Math.floor(lastOkTsSeconds * 1000));
      } else {
        setTimestampMs(Date.now());
      }
    } catch (error) {
      console.error("Error fetching /status:", error);
    } finally {
      inFlightRef.current = false;
    }
  };

  useEffect(() => {
    if (!apiBase) return;

    loadStatus();

    const effectiveSeconds =
      typeof cacheRefreshSeconds === "number" && cacheRefreshSeconds > 0
        ? Math.max(cacheRefreshSeconds, 15)
        : 120;

    const interval = setInterval(loadStatus, effectiveSeconds * 1000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, cacheRefreshSeconds]);

  useEffect(() => {
    if (!apiBase) return;
    if (!selected) return;

    const effectiveSeconds =
      typeof cacheRefreshSeconds === "number" && cacheRefreshSeconds > 0
        ? Math.max(cacheRefreshSeconds, 15)
        : 60;

    const interval = setInterval(loadStatus, effectiveSeconds * 1000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, selected, cacheRefreshSeconds]);

  const openIncidentModal = (providerRow) => {
    setSelected(providerRow ?? null);
  };

  const normalizedFilter = useMemo(() => normalizePostalCode(postalFilter), [postalFilter]);
  const postalNeedleDigits = useMemo(() => normalizePostalNeedle(postalFilter), [postalFilter]);

  const filteredProviders = useMemo(() => {
    if (!normalizedFilter) return providers;

    const needleText = normalizedFilter.toLowerCase();
    const needleDigits = postalNeedleDigits; // may be "" if user types letters

    return providers.filter((p) => {
      // 1) Real postal match (preferred)
      if (needleDigits && needleDigits.length >= 3) {
        const allInc = [
          ...(Array.isArray(p?.active_incidents) ? p.active_incidents : []),
          ...(Array.isArray(p?.recent_incidents) ? p.recent_incidents : []),
        ];

        const hit = allInc.some((inc) => {
          const pcs = Array.isArray(inc?.postal_codes) ? inc.postal_codes : [];
          return pcs.some((pc) => normalizePostalNeedle(pc) === needleDigits);
        });

        if (hit) return true;
      }

      // 2) Fallback match (provider name / subject)
      const hay1 = String(p?.provider ?? "").toLowerCase();
      const hay2 = String(p?.subject ?? "").toLowerCase();
      return hay1.includes(needleText) || hay2.includes(needleText);
    });
  }, [providers, normalizedFilter, postalNeedleDigits]);

  return (
    <div className="dashboard-container">
      <h1 className="dashboard-title">Datacenter Network Status</h1>

      <div className="dashboard-meta-row">
        <p className="dashboard-updated">Last updated: {formattedTime}</p>

        <input
          id="postalFilter"
          type="text"
          value={postalFilter}
          onChange={(e) => setPostalFilter(e.target.value)}
          placeholder="Search Postal Code"
          inputMode="text"
          autoComplete="off"
          className="dashboard-search-input"
        />
      </div>

      <AutoRefreshWrapper refreshKey={timestampMs}>
        <div className="status-grid">
          {filteredProviders.map((row, index) => (
            <StatusCard
              key={row?.provider_id ?? index}
              incident={row}
              onClick={() => openIncidentModal(row)}
            />
          ))}
        </div>
      </AutoRefreshWrapper>

      <IncidentModal open={!!selected} providerStatus={selected} onClose={() => setSelected(null)} />
    </div>
  );
}