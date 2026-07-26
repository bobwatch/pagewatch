import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { Badge, EmptyState, Spinner } from "./common";

const FALLBACK_FORMATS = ["generic", "slack", "discord", "feishu", "dingtalk"];
const FALLBACK_EVENTS = ["change", "error", "all"];

export default function AlertsPanel({ toast, status, onDataChanged }) {
  const formats = status?.alert_formats || FALLBACK_FORMATS;
  const events = status?.alert_events || FALLBACK_EVENTS;

  const [channels, setChannels] = useState(null);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [format, setFormat] = useState("generic");
  const [eventKind, setEventKind] = useState("change");
  const [busy, setBusy] = useState({});

  const load = useCallback(async () => {
    try {
      setChannels(await api.alerts());
    } catch (err) {
      toast(err.message, "err");
      setChannels([]);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

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
    </section>
  );
}
