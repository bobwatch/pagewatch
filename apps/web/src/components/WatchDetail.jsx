import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Spinner, shortHash } from "./common";

function diffLineClass(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-head";
  if (line.startsWith("@@")) return "diff-hunk";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-del";
  return "diff-ctx";
}

export default function WatchDetail({ name, liveResult, onClose }) {
  const [view, setView] = useState("diff");
  const [diffData, setDiffData] = useState(null);
  const [historyData, setHistoryData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setDiffData(null);
    setHistoryData(null);
    setError(null);
    Promise.all([api.diff(name), api.history(name, 100)])
      .then(([diff, history]) => {
        if (cancelled) return;
        setDiffData(diff);
        setHistoryData(history);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [name, liveResult]);

  const diffText = diffData?.diff;

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer">
        <div className="drawer-head">
          <h3>{name}</h3>
          <button type="button" className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="drawer-tabs">
          <button type="button" className={`tab ${view === "diff" ? "tab-active" : ""}`}
                  onClick={() => setView("diff")}>
            Latest diff
          </button>
          <button type="button" className={`tab ${view === "history" ? "tab-active" : ""}`}
                  onClick={() => setView("history")}>
            History {historyData ? `(${historyData.total})` : ""}
          </button>
        </div>

        {error && <p className="form-error">{error}</p>}
        {!error && !diffData && <Spinner />}

        {view === "diff" && diffData && (
          <div className="drawer-body">
            {diffText ? (
              <>
                <p className="muted">
                  {diffData.previous_at?.slice(0, 19)} → {diffData.latest_at?.slice(0, 19)}
                </p>
                <div className="diff">
                  {diffText.split("\n").map((line, index) => (
                    <div key={index} className={diffLineClass(line)}>
                      {line || " "}
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <p className="muted">
                No diff yet — it appears once two distinct snapshots exist for this page.
              </p>
            )}
          </div>
        )}

        {view === "history" && historyData && (
          <div className="drawer-body">
            {historyData.history.length === 0 ? (
              <p className="muted">No checks recorded yet.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Timestamp</th>
                    <th>Hash</th>
                    <th>Length</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {historyData.history.map((entry, index) => {
                    const number = historyData.total - historyData.history.length + index + 1;
                    const isCurrent = entry.content_hash === historyData.latest.content_hash;
                    const isPrevious = entry.content_hash === historyData.previous.content_hash;
                    return (
                      <tr key={`${entry.timestamp}-${index}`}>
                        <td>{number}</td>
                        <td>{(entry.timestamp || "").slice(0, 19)}</td>
                        <td className="mono">{shortHash(entry.content_hash)}</td>
                        <td>{entry.text_length}</td>
                        <td>
                          {isCurrent && <Badge tone="ok">current</Badge>}
                          {!isCurrent && isPrevious && <Badge tone="muted">previous</Badge>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
