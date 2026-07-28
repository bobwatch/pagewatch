import { useState } from "react";
import { api } from "../api";
import { Spinner } from "./common";

function parsePatterns(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function isValidUrl(str) {
  try {
    const url = new URL(str.startsWith("//") ? "https:" + str : str);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

export default function WatchForm({ watch, toast, onDone, onCancel }) {
  const isEdit = Boolean(watch);
  const [url, setUrl] = useState(watch?.url || "");
  const [name, setName] = useState(watch?.name || "");
  const [selector, setSelector] = useState(watch?.selector || "");
  const [interval, setInterval] = useState(watch?.interval ?? 3600);
  const [patternsText, setPatternsText] = useState((watch?.ignore_patterns || []).join("\n"));
  const [checkNow, setCheckNow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [urlError, setUrlError] = useState(null);

  function validateUrl(value) {
    if (!value.trim()) {
      setUrlError("URL is required");
      return false;
    }
    const normalized = value.startsWith("http://") || value.startsWith("https://") ? value : "https://" + value;
    if (!isValidUrl(normalized)) {
      setUrlError("Invalid URL format");
      return false;
    }
    setUrlError(null);
    return true;
  }

  async function submit(event) {
    event.preventDefault();
    setError(null);
    if (!validateUrl(url)) return;
    setSaving(true);
    try {
      if (isEdit) {
        const payload = {};
        if (url !== watch.url) payload.url = url;
        if ((selector || null) !== (watch.selector || null)) payload.selector = selector || null;
        if (Number(interval) !== watch.interval) payload.interval = Number(interval);
        const patterns = parsePatterns(patternsText);
        if (JSON.stringify(patterns) !== JSON.stringify(watch.ignore_patterns || [])) {
          payload.ignore_patterns = patterns;
        }
        if (Object.keys(payload).length === 0) {
          onCancel();
          return;
        }
        const data = await api.updateWatch(watch.name, payload);
        toast(
          data.baseline_reset
            ? `Updated ${watch.name} — baseline will be re-established on the next check`
            : `Updated ${watch.name}`,
          "ok",
        );
      } else {
        const payload = {
          url,
          name: name.trim() || undefined,
          selector: selector || undefined,
          interval: Number(interval),
          ignore_patterns: parsePatterns(patternsText),
          check_now: checkNow,
        };
        const data = await api.addWatch(payload);
        if (data.result?.error) {
          toast(`Watch added, but the baseline check failed: ${data.result.error}`, "warn");
        } else {
          toast(`Added ${data.watch.name}`, "ok");
        }
      }
      onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onCancel()}>
      <form className="modal" onSubmit={submit}>
        <h3>{isEdit ? `Edit watch — ${watch.name}` : "Add watch"}</h3>

        <label htmlFor="wf-url">URL</label>
        <input id="wf-url" required placeholder="https://example.com/pricing"
               value={url} onChange={(e) => { setUrl(e.target.value); setUrlError(null); }}
               onBlur={() => url && validateUrl(url)} />
        {urlError && <p className="form-error">{urlError}</p>}

        {!isEdit && (
          <>
            <label htmlFor="wf-name">Name (optional)</label>
            <input id="wf-name" placeholder="derived from the domain if empty"
                   value={name} onChange={(e) => setName(e.target.value)} />
          </>
        )}

        <label htmlFor="wf-selector">CSS selector (optional)</label>
        <input id="wf-selector" placeholder=".price, #main article"
               value={selector} onChange={(e) => setSelector(e.target.value)} />

        <label htmlFor="wf-interval">Check interval (seconds)</label>
        <input id="wf-interval" type="number" min="1" step="1" required
               value={interval} onChange={(e) => setInterval(e.target.value)} />

        <label htmlFor="wf-patterns">Ignore patterns (regex, one per line)</label>
        <textarea id="wf-patterns" rows="3" placeholder={"Updated at \\d{4}\n\\d+ views"}
                  value={patternsText} onChange={(e) => setPatternsText(e.target.value)} />

        {!isEdit && (
          <label className="check-label">
            <input type="checkbox" checked={checkNow} onChange={(e) => setCheckNow(e.target.checked)} />
            Capture baseline immediately
          </label>
        )}

        {error && <p className="form-error">{error}</p>}

        <div className="modal-actions">
          <button type="button" className="btn" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? <Spinner /> : isEdit ? "Save changes" : "Add watch"}
          </button>
        </div>
      </form>
    </div>
  );
}