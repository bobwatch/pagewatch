import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Spinner } from "./common";

const TAG_COLORS = ["#22d3ee", "#f59e0b", "#ef4444", "#10b981", "#8b5cf6", "#ec4899", "#06b6d4", "#84cc16"];

function tagColor(tag, index) {
  return TAG_COLORS[index % TAG_COLORS.length];
}

export default function TagsPanel({ toast, status, onDataChanged }) {
  const [watches, setWatches] = useState(null);
  const [selectedTag, setSelectedTag] = useState(null);

  const load = useCallback(async () => {
    try {
      setWatches(await api.watches());
    } catch (err) {
      toast(err.message, "err");
      setWatches([]);
    }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const allTags = status?.tags || [];
  const filtered = selectedTag
    ? (watches || []).filter((w) => (w.tags || []).includes(selectedTag))
    : (watches || []);

  return (
    <section>
      <div className="toolbar">
        <h2>Tags</h2>
      </div>

      <div className="card form-card">
        <h3>All Tags</h3>
        {allTags.length === 0 ? (
          <p className="muted">No tags configured. Edit a watch to add tags.</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", marginTop: "8px" }}>
            <button type="button" className={`btn btn-sm ${!selectedTag ? "btn-primary" : ""}`}
                    onClick={() => setSelectedTag(null)}>All</button>
            {allTags.map((tag, i) => (
              <button key={tag} type="button"
                      className={`btn btn-sm ${selectedTag === tag ? "btn-primary" : ""}`}
                      onClick={() => setSelectedTag(tag)}
                      style={{ borderLeft: `3px solid ${tagColor(tag, i)}` }}>
                {tag}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="card" style={{ marginTop: "1.5rem" }}>
        <h3>{selectedTag ? `Watches tagged "${selectedTag}"` : "All Watches"}</h3>
        {filtered.length === 0 ? (
          <p className="muted">No watches found.</p>
        ) : (
          <table>
            <thead><tr><th>Name</th><th>URL</th><th>Tags</th><th>Status</th></tr></thead>
            <tbody>
              {filtered.map((w) => (
                <tr key={w.name} className={w.paused ? "row-muted" : ""}>
                  <td>{w.name}</td>
                  <td className="cell-url">{w.url}</td>
                  <td>{(w.tags || []).map((t, i) => (
                    <span key={t} className="tag-badge" style={{ background: tagColor(t, i) + "33", color: tagColor(t, i), border: `1px solid ${tagColor(t, i)}` }}>
                      {t}
                    </span>
                  ))}</td>
                  <td>{w.paused ? <Badge tone="warn">paused</Badge> : w.last_hash ? <Badge tone="ok">active</Badge> : <Badge tone="muted">pending</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}