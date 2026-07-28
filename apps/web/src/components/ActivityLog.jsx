import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Spinner, Badge, timeAgo } from "./common";

export default function ActivityLog({ toast }) {
  const [watches, setWatches] = useState(null);
  const [snapshots, setSnapshots] = useState({});
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const w = await api.watches();
      setWatches(w);
      const snapMap = {};
      for (const watch of w.slice(0, 20)) {
        try {
          const h = await api.history(watch.name, 10);
          snapMap[watch.name] = h.history || [];
        } catch { /* skip */ }
      }
      setSnapshots(snapMap);
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <Spinner />;

  const allEntries = [];
  for (const watch of watches || []) {
    for (const entry of snapshots[watch.name] || []) {
      allEntries.push({ ...entry, watch: watch.name, url: watch.url });
    }
  }
  allEntries.sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
  const recent = allEntries.slice(0, 100);

  return (
    <section>
      <div className="toolbar"><h2>Activity Log</h2></div>
      {recent.length === 0 ? (
        <p className="muted">No activity recorded yet. Run a check first.</p>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr><th>Time</th><th>Watch</th><th>Hash</th><th>Length</th></tr>
            </thead>
            <tbody>
              {recent.map((e, i) => (
                <tr key={`${e.watch}-${e.timestamp}-${i}`}>
                  <td>{(e.timestamp || "").slice(0, 19)}</td>
                  <td>{e.watch}</td>
                  <td className="mono">{(e.content_hash || "").slice(0, 12)}</td>
                  <td>{e.text_length}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}