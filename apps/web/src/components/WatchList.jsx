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
  const [noAlerts, setNoAlerts] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(new Set());

  const load = useCallback(async () => {
    try {
      setWatches(await api.watches(search || null));
    } catch (err) {
      toast(`Failed to load watches: ${err.message}`, "err");
      setWatches([]);
    }
  }, [toast, search]);

  useEffect(() => {
    load();
  }, [load]);

  const setBusyFlag = (key, value) =>
    setBusy((current) => ({ ...current, [key]: value }));

  async function checkAll() {
    setBusyFlag("__all__", true);
    try {
      const data = await api.checkAll(!noAlerts);
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
      const data = await api.checkWatch(name, !noAlerts);
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
    const key = `pause-${watch.name}`;
    setBusyFlag(key, true);
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
    } finally {
      setBusyFlag(key, false);
    }
  }

  async function formDone() {
    setShowAdd(false);
    setEditing(null);
    await load();
    onDataChanged();
  }

  function toggleSelect(name) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleSelectAll() {
    if (!watches) return;
    if (selected.size === watches.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(watches.map((w) => w.name)));
    }
  }

  async function batchOp(op) {
    if (selected.size === 0) return;
    const names = Array.from(selected);
    setBusyFlag("__batch__", true);
    try {
      let fn;
      if (op === "pause") fn = api.batchPause(names);
      else if (op === "resume") fn = api.batchResume(names);
      else if (op === "delete") { if (!window.confirm(`Delete ${names.length} watch(es)?`)) return; fn = api.batchDelete(names); }
      else if (op === "check") fn = api.batchCheck(names);
      await fn;
      toast(`Batch ${op} completed for ${names.length} watch(es)`, "ok");
      setSelected(new Set());
      await load();
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusyFlag("__batch__", false);
    }
  }

  async function cloneWatch(name) {
    const newName = prompt(`Clone "${name}" as:`, `${name}-copy`);
    if (!newName) return;
    try {
      await api.cloneWatch(name, { name: newName });
      toast(`Cloned ${name} → ${newName}`, "ok");
      await load();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Watches</h2>
        <div className="toolbar-actions">
          <input type="search" placeholder="Search name or URL..." value={search}
                 onChange={(e) => setSearch(e.target.value)}
                 style={{ padding: "4px 8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--bg)", color: "var(--fg)", fontSize: "13px", width: "200px" }} />
          {selected.size > 0 && (
            <>
              <span className="muted" style={{ fontSize: "12px" }}>{selected.size} selected</span>
              <button type="button" className="btn btn-sm" onClick={() => batchOp("pause")} disabled={busy.__batch__}>Pause</button>
              <button type="button" className="btn btn-sm" onClick={() => batchOp("resume")} disabled={busy.__batch__}>Resume</button>
              <button type="button" className="btn btn-sm" onClick={() => batchOp("check")} disabled={busy.__batch__}>Check</button>
              <button type="button" className="btn btn-sm btn-danger" onClick={() => batchOp("delete")} disabled={busy.__batch__}>Delete</button>
            </>
          )}
          <label className="check-label" style={{ fontSize: "12px", marginRight: "8px" }}>
            <input type="checkbox" checked={noAlerts} onChange={(e) => setNoAlerts(e.target.checked)} />
            Suppress alerts
          </label>
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
                    <th><input type="checkbox" onChange={toggleSelectAll}
                               checked={watches && watches.length > 0 && selected.size === watches.length} /></th>
                    <th>Name</th>
                    <th>URL</th>
                    <th>Interval</th>
                    <th>Tags</th>
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
                      <td><input type="checkbox" checked={selected.has(watch.name)}
                                 onChange={() => toggleSelect(watch.name)} /></td>
                      <td className="cell-name">
                        {watch.name}
                        {watch.render && <Badge tone="muted" title="Rendered with Playwright (JS)">JS</Badge>}
                      </td>
                      <td className="cell-url">
                        <a href={watch.url} target="_blank" rel="noreferrer" title={watch.url}>
                          {watch.url}
                        </a>
                        {watch.selector && <span className="selector" title="CSS selector">{watch.selector}</span>}
                      </td>
                      <td>{watch.interval}s</td>
                      <td>{(watch.tags || []).map((t) => (
                        <span key={t} className="tag-badge-sm">{t}</span>
                      ))}</td>
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
                                title={watch.paused ? "Resume" : "Pause"}
                                aria-label={watch.paused ? "Resume watch" : "Pause watch"}
                                disabled={busy[`pause-${watch.name}`]}>
                          {busy[`pause-${watch.name}`] ? <Spinner /> : watch.paused ? "▶" : "⏸"}
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => cloneWatch(watch.name)} title="Clone">
                          ◎
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
