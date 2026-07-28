import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import AlertsPanel from "./components/AlertsPanel";
import SettingsPanel from "./components/SettingsPanel";
import WatchList from "./components/WatchList";
import DataPanel from "./components/DataPanel";
import StatsPanel from "./components/StatsPanel";
import ActivityLog from "./components/ActivityLog";
import NotificationsPanel from "./components/NotificationsPanel";
import TagsPanel from "./components/TagsPanel";

const TABS = ["Watches", "Stats", "Activity", "Alerts", "Notifications", "Tags", "Data", "Settings"];

export default function App() {
  const [tab, setTab] = useState("Watches");
  const [status, setStatus] = useState(null);
  const [toasts, setToasts] = useState([]);

  const toast = useCallback((message, tone = "ok") => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setToasts((current) => [...current, { id, message, tone }]);
    setTimeout(() => {
      setToasts((current) => current.filter((t) => t.id !== id));
    }, 4500);
  }, []);

  const refreshStatus = useCallback(() => {
    api.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <img className="brand-logo" src="/logo.svg" alt="" />
          <span className="brand-name">
            PageWatch<span className="brand-tld">.tech</span>
          </span>
          {status && <span className="version">v{status.version}</span>}
        </div>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((name) => (
            <button
              key={name}
              type="button"
              className={`tab ${tab === name ? "tab-active" : ""}`}
              onClick={() => setTab(name)}
            >
              {name}
              {name === "Watches" && status ? ` (${status.watch_count})` : ""}
            </button>
          ))}
        </nav>
        <a className="ext-link" href="https://pagewatch.tech" target="_blank" rel="noreferrer">
          pagewatch.tech ↗
        </a>
      </header>

      <main className="content">
        {tab === "Watches" && <WatchList toast={toast} onDataChanged={refreshStatus} />}
        {tab === "Stats" && <StatsPanel toast={toast} />}
        {tab === "Activity" && <ActivityLog toast={toast} />}
        {tab === "Alerts" && <AlertsPanel toast={toast} status={status} onDataChanged={refreshStatus} />}
        {tab === "Notifications" && <NotificationsPanel toast={toast} />}
        {tab === "Tags" && <TagsPanel toast={toast} status={status} onDataChanged={refreshStatus} />}
        {tab === "Data" && <DataPanel toast={toast} status={status} onDataChanged={refreshStatus} />}
        {tab === "Settings" && <SettingsPanel toast={toast} status={status} />}
      </main>

      <div className="toasts" role="status">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`}>
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}
