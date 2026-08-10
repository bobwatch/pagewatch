import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Spinner, Badge } from "./common";

function formatBytes(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = n;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${size} B` : `${size.toFixed(1)} ${units[unit]}`;
}

export default function StatsPanel({ toast }) {
  const [stats, setStats] = useState(null);

  const load = useCallback(async () => {
    try {
      setStats(await api.stats());
    } catch (err) {
      toast(err.message, "err");
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  if (!stats) return <Spinner />;

  return (
    <section>
      <div className="toolbar"><h2>Statistics</h2></div>

      <div className="card-grid">
        <div className="stat-card"><h3>{stats.total_watches}</h3><p>Total Watches</p></div>
        <div className="stat-card"><h3>{stats.active_watches}</h3><p>Active</p></div>
        <div className="stat-card"><h3>{stats.paused_watches}</h3><p>Paused</p></div>
        <div className="stat-card" style={{ borderColor: stats.errored_watches > 0 ? "var(--err)" : "" }}>
          <h3>{stats.errored_watches}</h3><p>Errored</p>
        </div>
        <div className="stat-card"><h3>{stats.total_checks}</h3><p>Total Checks</p></div>
        <div className="stat-card" style={{ borderColor: stats.error_rate > 5 ? "var(--err)" : "" }}>
          <h3>{stats.error_rate}%</h3><p>Error Rate</p>
        </div>
        <div className="stat-card"><h3>{stats.changes_today}</h3><p>Changes Today</p></div>
        <div className="stat-card"><h3>{stats.changes_week}</h3><p>Changes This Week</p></div>
        <div className="stat-card"><h3>{stats.changes_month}</h3><p>Changes This Month</p></div>
        {stats.disk_usage && (
          <div className="stat-card"><h3>{formatBytes(stats.disk_usage.total_bytes)}</h3><p>Disk Usage</p></div>
        )}
      </div>

      {stats.top_changed?.length > 0 && (
        <div className="card" style={{ marginTop: "1.5rem" }}>
          <h3>Most Active Watches</h3>
          <table>
            <thead><tr><th>#</th><th>Name</th><th>Snapshots</th></tr></thead>
            <tbody>
              {stats.top_changed.map((w, i) => (
                <tr key={w.name}><td>{i + 1}</td><td>{w.name}</td><td>{w.snapshots}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {stats.disk_usage?.per_watch?.length > 0 && (
        <div className="card" style={{ marginTop: "1.5rem" }}>
          <h3>Storage by Watch</h3>
          <table>
            <thead><tr><th>#</th><th>Name</th><th>Size</th></tr></thead>
            <tbody>
              {stats.disk_usage.per_watch.slice(0, 5).map((w, i) => (
                <tr key={w.name}><td>{i + 1}</td><td>{w.name}</td><td>{formatBytes(w.bytes)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}