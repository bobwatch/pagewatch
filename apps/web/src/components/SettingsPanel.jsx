import { useEffect, useState } from "react";
import { api } from "../api";
import { Spinner } from "./common";

export default function SettingsPanel({ toast, status }) {
  const [config, setConfig] = useState(null);
  const [interval, setInterval] = useState("");
  const [retries, setRetries] = useState("");
  const [errorThreshold, setErrorThreshold] = useState("");
  const [proxy, setProxy] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .config()
      .then((cfg) => {
        setConfig(cfg);
        setInterval(String(cfg.interval ?? 3600));
        setRetries(String(cfg.retries ?? 2));
        setErrorThreshold(String(cfg.error_threshold ?? 1));
        setProxy(cfg.proxy || "");
      })
      .catch((err) => toast(err.message, "err"));
  }, [toast]);

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    try {
      const updated = await api.saveConfig({
        interval: Number(interval),
        retries: Number(retries),
        error_threshold: Number(errorThreshold),
        proxy: proxy.trim() || null,
      });
      setConfig(updated);
      toast("Settings saved", "ok");
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setSaving(false);
    }
  }

  if (!config) return <Spinner />;

  return (
    <section>
      <div className="toolbar">
        <h2>Settings</h2>
      </div>

      <div className="card form-card">
        <form onSubmit={save} className="settings-form">
          <div className="field">
            <label htmlFor="cf-interval">Default check interval (seconds)</label>
            <input id="cf-interval" type="number" min="1" required
                   value={interval} onChange={(e) => setInterval(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="cf-retries">Fetch retries (0–10)</label>
            <input id="cf-retries" type="number" min="0" max="10" required
                   value={retries} onChange={(e) => setRetries(e.target.value)} />
            <p className="muted">Connection errors and 5xx responses retry with exponential backoff.</p>
          </div>
          <div className="field">
            <label htmlFor="cf-error-threshold">Error alert threshold</label>
            <input id="cf-error-threshold" type="number" min="1" required
                   value={errorThreshold} onChange={(e) => setErrorThreshold(e.target.value)} />
            <p className="muted">
              Alert once after this many consecutive check failures; a recovery notice is sent when the watch succeeds again.
            </p>
          </div>
          <div className="field">
            <label htmlFor="cf-proxy">HTTP(S) proxy (empty = direct)</label>
            <input id="cf-proxy" placeholder="http://127.0.0.1:7890"
                   value={proxy} onChange={(e) => setProxy(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? <Spinner /> : "Save settings"}
            </button>
          </div>
        </form>
      </div>

      <div className="card info-card">
        <h3>About this instance</h3>
        <dl>
          <dt>Version</dt>
          <dd>{status?.version || "—"}</dd>
          <dt>Data directory</dt>
          <dd className="mono">{status?.data_dir || "—"}</dd>
          <dt>Storage</dt>
          <dd>Plain JSON files — no database. Back up with <code>pagewatch export</code>.</dd>
        </dl>
      </div>
    </section>
  );
}
