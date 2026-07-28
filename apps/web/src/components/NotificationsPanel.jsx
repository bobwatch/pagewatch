import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Spinner, Badge, timeAgo } from "./common";

export default function NotificationsPanel({ toast }) {
  const [history, setHistory] = useState(null);

  const load = useCallback(async () => {
    try {
      setHistory(await api.alertsHistory());
    } catch (err) {
      toast(err.message, "err");
      setHistory({ total: 0, history: [] });
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  if (!history) return <Spinner />;

  return (
    <section>
      <div className="toolbar">
        <h2>Notification History</h2>
        <span className="muted">Last {history.history?.length || 0} of {history.total || 0}</span>
      </div>
      {(!history.history || history.history.length === 0) ? (
        <p className="muted">No notifications sent yet.</p>
      ) : (
        <div className="card">
          <table>
            <thead>
              <tr><th>Time</th><th>Watch</th><th>Event</th><th>Channel</th><th>Status</th></tr>
            </thead>
            <tbody>
              {[...history.history].reverse().map((e, i) => (
                <tr key={`${e.timestamp}-${i}`}>
                  <td>{(e.timestamp || "").slice(0, 19)}</td>
                  <td>{e.watch}</td>
                  <td><Badge tone={e.event === "error" ? "err" : e.event === "change" ? "warn" : "muted"}>{e.event}</Badge></td>
                  <td>{e.channel}</td>
                  <td>
                    {e.ok ? <Badge tone="ok">sent</Badge> : <Badge tone="err" title={e.error || ""}>failed</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}