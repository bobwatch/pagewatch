import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Badge, EmptyState, Spinner } from "./common";

const FALLBACK_FORMATS = ["generic", "slack", "discord", "feishu", "dingtalk"];
const FALLBACK_EVENTS = ["change", "error", "all"];

export default function AlertsPanel({ toast, status, onDataChanged }) {
  const formats = status?.alert_formats || FALLBACK_FORMATS;
  const events = status?.alert_events || FALLBACK_EVENTS;
  const emailConfigured = status?.email_configured;

  const [channels, setChannels] = useState(null);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [format, setFormat] = useState("generic");
  const [eventKind, setEventKind] = useState("change");
  const [busy, setBusy] = useState({});

  // Email config state
  const [emailCfg, setEmailCfg] = useState(null);
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("587");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPass, setSmtpPass] = useState("");
  const [smtpTls, setSmtpTls] = useState(true);
  const [fromAddr, setFromAddr] = useState("");
  const [toAddrs, setToAddrs] = useState("");
  const [emailSaving, setEmailSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setChannels(await api.alerts());
    } catch (err) {
      toast(err.message, "err");
      setChannels([]);
    }
  }, [toast]);

  const loadEmail = useCallback(async () => {
    try {
      const cfg = await api.emailConfig();
      setEmailCfg(cfg);
      setSmtpHost(cfg.smtp_host || "");
      setSmtpPort(String(cfg.smtp_port || 587));
      setSmtpUser(cfg.smtp_user || "");
      setSmtpTls(cfg.smtp_tls !== false);
      setFromAddr(cfg.from_addr || "");
      setToAddrs(cfg.to_addrs || "");
    } catch {
      // email not configured
    }
  }, []);

  useEffect(() => {
    load();
    loadEmail();
  }, [load, loadEmail]);

  async function addChannel(event) {
    event.preventDefault();
    try {
      const channel = await api.addAlert({
        url,
        name: name.trim() || undefined,
        format,
        events: eventKind,
      });
      toast(`Added alert channel ${channel.name}`, "ok");
      setUrl("");
      setName("");
      await load();
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function removeChannel(channelName) {
    if (!window.confirm(`Remove alert channel "${channelName}"?`)) return;
    try {
      await api.deleteAlert(channelName);
      toast(`Removed ${channelName}`, "ok");
      await load();
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    }
  }

  async function testChannel(channelName) {
    const key = channelName || "__all__";
    setBusy((current) => ({ ...current, [key]: true }));
    try {
      const data = await api.testAlerts(channelName);
      for (const delivery of data.deliveries) {
        if (delivery.ok) toast(`Test delivered via ${delivery.channel} (HTTP ${delivery.status})`, "ok");
        else toast(`Test failed via ${delivery.channel}: ${delivery.error}`, "err");
      }
      if (data.deliveries.length === 0) toast("No channels to test", "warn");
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setBusy((current) => ({ ...current, [key]: false }));
    }
  }

  async function saveEmailConfig(event) {
    event.preventDefault();
    setEmailSaving(true);
    try {
      const saved = await api.saveEmailConfig({
        smtp_host: smtpHost,
        smtp_port: Number(smtpPort),
        smtp_user: smtpUser || null,
        smtp_pass: smtpPass || null,
        smtp_tls: smtpTls,
        from_addr: fromAddr || null,
        to_addrs: toAddrs,
      });
      setEmailCfg(saved);
      toast("Email settings saved", "ok");
      onDataChanged();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      setEmailSaving(false);
    }
  }

  return (
    <section>
      <div className="toolbar">
        <h2>Alert channels</h2>
        <button type="button" className="btn" onClick={() => testChannel(null)}
                disabled={busy.__all__ || !channels?.length}>
          {busy.__all__ ? <Spinner /> : "Test all"}
        </button>
      </div>

      <div className="card form-card">
        <form className="inline-form" onSubmit={addChannel}>
          <div className="field grow">
            <label htmlFor="al-url">Webhook URL</label>
            <input id="al-url" required placeholder="https://hooks.slack.com/services/…"
                   value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="al-name">Name</label>
            <input id="al-name" placeholder="webhook-N" value={name}
                   onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="al-format">Format</label>
            <select id="al-format" value={format} onChange={(e) => setFormat(e.target.value)}>
              {formats.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="al-events">Events</label>
            <select id="al-events" value={eventKind} onChange={(e) => setEventKind(e.target.value)}>
              {events.map((ev) => <option key={ev} value={ev}>{ev}</option>)}
            </select>
          </div>
          <button type="submit" className="btn btn-primary">Add channel</button>
        </form>
      </div>

      {!channels && <Spinner />}

      {channels && channels.length === 0 && (
        <EmptyState
          title="No alert channels yet"
          hint="Add a Slack / Discord / Feishu / DingTalk webhook, or any endpoint that accepts JSON."
        />
      )}

      {channels && channels.length > 0 && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Format</th>
                <th>Events</th>
                <th>URL</th>
                <th className="col-actions">Actions</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((channel) => (
                <tr key={channel.name}>
                  <td className="cell-name">{channel.name}</td>
                  <td><Badge tone="muted">{channel.format || "generic"}</Badge></td>
                  <td>{channel.events || "change"}</td>
                  <td className="cell-url" title={channel.url}>{channel.url}</td>
                  <td className="col-actions">
                    <button type="button" className="btn btn-sm" onClick={() => testChannel(channel.name)}
                            disabled={busy[channel.name]}>
                      {busy[channel.name] ? <Spinner /> : "Test"}
                    </button>
                    <button type="button" className="btn btn-sm btn-danger"
                            onClick={() => removeChannel(channel.name)}>
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card form-card" style={{ marginTop: "1.5rem" }}>
        <h3>Email alerts (SMTP)</h3>
        <form onSubmit={saveEmailConfig} className="settings-form">
          <div className="field">
            <label htmlFor="smtp-host">SMTP host</label>
            <input id="smtp-host" required placeholder="smtp.gmail.com"
                   value={smtpHost} onChange={(e) => setSmtpHost(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="smtp-port">SMTP port</label>
            <input id="smtp-port" type="number" min="1" max="65535" required
                   value={smtpPort} onChange={(e) => setSmtpPort(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="smtp-user">SMTP user (optional)</label>
            <input id="smtp-user" placeholder="user@gmail.com"
                   value={smtpUser} onChange={(e) => setSmtpUser(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="smtp-pass">SMTP password (optional)</label>
            <input id="smtp-pass" type="password" placeholder={emailCfg?.smtp_pass_set ? "(unchanged)" : ""}
                   value={smtpPass} onChange={(e) => setSmtpPass(e.target.value)} />
          </div>
          <label className="check-label">
            <input type="checkbox" checked={smtpTls} onChange={(e) => setSmtpTls(e.target.checked)} />
            Use TLS (STARTTLS)
          </label>
          <div className="field">
            <label htmlFor="from-addr">From address (optional)</label>
            <input id="from-addr" placeholder="pagewatch@example.com"
                   value={fromAddr} onChange={(e) => setFromAddr(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="to-addrs">Recipients (comma-separated)</label>
            <input id="to-addrs" required placeholder="alerts@example.com, team@example.com"
                   value={toAddrs} onChange={(e) => setToAddrs(e.target.value)} />
          </div>
          <div>
            <button type="submit" className="btn btn-primary" disabled={emailSaving}>
              {emailSaving ? <Spinner /> : "Save email settings"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
