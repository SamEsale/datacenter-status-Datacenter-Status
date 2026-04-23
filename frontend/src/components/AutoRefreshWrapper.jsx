// frontend/src/components/AutoRefreshWrapper.jsx

import { useEffect, useRef, useState } from "react";
import "./AutoRefreshWrapper.css";

export default function AutoRefreshWrapper({ children, refreshKey }) {
  const [fade, setFade] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    // Guard: ignore undefined/null keys
    if (refreshKey === undefined || refreshKey === null) return;

    // Clear any previous timer before starting a new fade cycle
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    setFade(true);

    timerRef.current = setTimeout(() => {
      setFade(false);
      timerRef.current = null;
    }, 350);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [refreshKey]);

  return (
    <div className={`auto-refresh-wrapper ${fade ? "fade" : ""}`}>
      {children}
    </div>
  );
}