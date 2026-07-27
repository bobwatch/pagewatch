import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import WatchDetail from "./WatchDetail";
import WatchForm from "./WatchForm";
import { Badge, EmptyState, Spinner, timeAgo } from "./common";

function statusBadge(watch, result) {
  if (watch.paused) return <Badge tone="warn">paused</Badge>;
  if (result?.error) return <Badge tone="err" title={result.error}>error</Badge>;
  if (result?.changed) return <Badge tone="warn">CHANGED</Badge>;
  if (result) return <Badge tone="ok">no change</Badge>;
  if (watch.last_hash) return <Badge tone="ok">active</Badge>;
  return <Badge tone="muted">pending</Badge>;
}

export default function WatchList({ toast, onDataChanged }) {
  const [watches, setWatches] = useState(null);
  const [results, setResults] = useState({});
  const [busy, setBusy] = useState({});
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState(null);
  const [detailName, setDetailName] = useState(null);

  const load = useCallback(async () => {
    try {
      setWatches(await api.watches());
    } catch (err) {
      toast(`Failed to load watches: ${err.message}`, "err");
      setWatches([]);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const setBusyFlag = (key, value) =>
    setBusy((current) => ({ ...current, [key]: value }));

  async function checkAll() {
    setBusyFlag("__all__", true);
    try {
      const data = await api.checkAll();
      const map = {};
      for (const result of data.results) map[result.name] = result;
      setResults(map);
      const changed = data.results.filter((r) => r.changed).length;
      const failed = data.results.filter((r) => r.error).length;
      const sent = data.alerts.filter((a) => a.ok).length;
      let message = `Checked ${data.results.length} — ${changed} changed`;
      if (failed) message += `, ${failed} failed`;
      if (sent) message += `, ${sent} alert(s) sent`;
      toast(message, changed ? "warn" : "ok");
      await load();
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusyFlag("__all__", false);
    }
  }

  async function checkOne(name) {
    setBusyFlag(name, true);
    try {
      const data = await api.checkWatch(name);
      setResults((current) => ({ ...current, [name]: data.result }));
      if (data.result.error) toast(`${name}: ${data.result.error}`, "err");
      else if (data.result.changed) toast(`${name} changed`, "warn");
      else toast(`${name}: no change`, "ok");
      await load();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusyFlag(name, false);
    }
  }

  async function removeWatch(name) {
    if (!window.confirm(`Delete watch "${name}" and its snapshot history?`)) return;
    try {
      await api.deleteWatch(name);
      toast(`Removed ${name}`, "ok");
      if (detailName === name) setDetailName(null);
      await load();
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function togglePause(watch) {
    try {
      if (watch.paused) {
        await api.resumeWatch(watch.name);
        toast(`Resumed ${watch.name}`, "ok");
      } else {
        await api.pauseWatch(watch.name);
        toast(`Paused ${watch.name}`, "warn");
      }
      await load();
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function formDone() {
    setShowAdd(false);
    setEditing(null);
    await load();
    onDataChanged();
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Watches</h2>
        <div className="toolbar-actions">
          <button type="button" className="btn" onClick={checkAll} disabled={busy.__all__ || !watches?.length}>
            {busy.__all__ ? <Spinner /> : "Check all"}
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setShowAdd(true)}>
            + Add watch
          </button>
        </div>
      </div>

      {!watches && <Spinner />}

      {watches && watches.length === 0 && (
        <EmptyState
          title="No pages monitored yet"
          hint="Add a URL to start tracking changes — CSS selectors and ignore patterns are supported."
        />
      )}

      {watches && watches.length > 0 && (
        <div className="card">
<table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>URL</th>
                    <th>Interval</th>
                    <th>Ignores</th>
                    <th>Checks</th>
                    <th>Errors</th>
                    <th>Last checked</th>
                    <th>Status</th>
                    <th className="col-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {watches.map((watch) => (
                    <tr key={watch.name} className={watch.paused ? "row-muted" : ""}>
                      <td className="cell-name">{watch.name}</td>
                      <td className="cell-url">
                        <a href={watch.url} target="_blank" rel="noreferrer" title={watch.url}>
                          {watch.url}
                        </a>
                        {watch.selector && <span className="selector" title="CSS selector">{watch.selector}</span>}
                      </td>
                      <td>{watch.interval}s</td>
                      <td>{watch.ignore_patterns?.length || "—"}</td>
                      <td>{watch.check_count ?? 0}</td>
                      <td>{watch.error_count ?? 0}</td>
                      <td title={watch.last_checked || ""}>{timeAgo(watch.last_checked)}</td>
                      <td>{statusBadge(watch, results[watch.name])}</td>
                      <td className="col-actions">
                        <button type="button" className="btn btn-sm" onClick={() => checkOne(watch.name)}
                                disabled={busy[watch.name] || watch.paused}>
                          {busy[watch.name] ? <Spinner /> : "Check"}
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => togglePause(watch)}
                                title={watch.paused ? "Resume" : "Pause"}>
                          {watch.paused ? "▶" : "⏸"}
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => setDetailName(watch.name)}>
                          Details
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => setEditing(watch)}>
                          Edit
                        </button>
                        <button type="button" className="btn btn-sm btn-danger" title="Delete"
                                onClick={() => removeWatch(watch.name)}>
                          ✕
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
        </div>
      )}

      {showAdd && <WatchForm toast={toast} onDone={formDone} onCancel={() => setShowAdd(false)} />}
      {editing && (
        <WatchForm watch={editing} toast={toast} onDone={formDone} onCancel={() => setEditing(null)} />
      )}
      {detailName && (
        <WatchDetail
          name={detailName}
          liveResult={results[detailName]}
          onClose={() => setDetailName(null)}
        />
      )}
    </section>
  );
}
