import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Spinner, Badge } from "./common";

export default function DataPanel({ toast, status, onDataChanged }) {
  const [daemonRunning, setDaemonRunning] = useState(false);
  const [daemonBusy, setDaemonBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [watches, setWatches] = useState(null);
  const [clearName, setClearName] = useState("");
  const [clearing, setClearing] = useState(false);

  const loadDaemon = useCallback(async () => {
    try {
      const data = await api.daemonStatus();
      setDaemonRunning(data.running);
    } catch {
      // daemon endpoint may not exist in older versions
    }
  }, []);

  const loadWatches = useCallback(async () => {
    try {
      setWatches(await api.watches());
    } catch {
      setWatches([]);
    }
  }, []);

  useEffect(() => {
    loadDaemon();
    loadWatches();
  }, [loadDaemon, loadWatches]);

  async function toggleDaemon() {
    setDaemonBusy(true);
    try {
      if (daemonRunning) {
        await api.daemonStop();
        setDaemonRunning(false);
        toast("Daemon stopped", "ok");
      } else {
        await api.daemonStart();
        setDaemonRunning(true);
        toast("Daemon started — monitoring in background", "ok");
      }
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setDaemonBusy(false);
    }
  }

  async function handleExport() {
    setExporting(true);
    try {
      const data = await api.exportData();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `pagewatch-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast(`Exported ${data.watches.length} watch(es)`, "ok");
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setExporting(false);
    }
  }

  async function handleImport(event) {
    event.preventDefault();
    const file = event.target.file.files[0];
    if (!file) return;
    setImporting(true);
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const result = await api.importData({ ...data, replace: event.target.replace.checked });
      toast(`Imported ${result.imported}, skipped ${result.skipped}, restored ${result.restored} snapshot(s)`, "ok");
      onDataChanged();
      loadWatches();
    } catch (err) {
      toast(`Import failed: ${err.message}`, "err");
    } finally {
      setImporting(false);
      event.target.reset();
    }
  }

  async function handleClearHistory() {
    if (!clearName) return;
    if (!window.confirm(`Delete all snapshot history for "${clearName}"? This cannot be undone.`)) return;
    setClearing(true);
    try {
      await api.deleteHistory(clearName);
      toast(`History cleared for ${clearName}`, "ok");
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setClearing(false);
    }
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Data Management</h2>
      </div>

      <div className="card form-card">
        <h3>Background Daemon</h3>
        <p className="muted">
          The daemon continuously monitors all active watches on their intervals and dispatches alerts.
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", marginTop: "8px" }}>
          <Badge tone={daemonRunning ? "ok" : "muted"}>{daemonRunning ? "Running" : "Stopped"}</Badge>
          <button type="button" className="btn" onClick={toggleDaemon} disabled={daemonBusy}>
            {daemonBusy ? <Spinner /> : daemonRunning ? "Stop daemon" : "Start daemon"}
          </button>
        </div>
      </div>

      <div className="card form-card" style={{ marginTop: "1.5rem" }}>
        <h3>Export / Import</h3>
        <p className="muted">
          Export all watches and snapshot history as a JSON file. Import to restore on another instance.
        </p>
        <div style={{ display: "flex", gap: "12px", marginTop: "8px" }}>
          <button type="button" className="btn" onClick={handleExport} disabled={exporting}>
            {exporting ? <Spinner /> : "Download backup"}
          </button>
        </div>

        <form onSubmit={handleImport} style={{ marginTop: "12px" }}>
          <div className="field">
            <label htmlFor="import-file">Restore from backup file</label>
            <input id="import-file" name="file" type="file" accept=".json" required />
          </div>
          <label className="check-label">
            <input type="checkbox" name="replace" defaultChecked={false} />
            Replace existing watches (instead of merge)
          </label>
          <div style={{ marginTop: "8px" }}>
            <button type="submit" className="btn btn-primary" disabled={importing}>
              {importing ? <Spinner /> : "Upload & restore"}
            </button>
          </div>
        </form>
      </div>

      <div className="card form-card" style={{ marginTop: "1.5rem" }}>
        <h3>Snapshot History</h3>
        <p className="muted">
          Clear all historical snapshots for a specific watch. The current baseline is preserved.
        </p>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "8px" }}>
          <select value={clearName} onChange={(e) => setClearName(e.target.value)}
                  style={{ flex: 1, maxWidth: "300px" }}>
            <option value="">— Select a watch —</option>
            {(watches || []).map((w) => (
              <option key={w.name} value={w.name}>{w.name}</option>
            ))}
          </select>
          <button type="button" className="btn btn-danger" onClick={handleClearHistory}
                  disabled={!clearName || clearing}>
            {clearing ? <Spinner /> : "Clear history"}
          </button>
        </div>
      </div>
    </section>
  );
}