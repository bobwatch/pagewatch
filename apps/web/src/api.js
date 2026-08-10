async function request(method, path, body, retried = false) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = localStorage.getItem("pagewatch_token");
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(path, {
    method,
    headers: Object.keys(headers).length ? headers : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !retried) {
    const entered = window.prompt(
      "This pagewatch server requires an access token (PAGEWATCH_TOKEN). Enter it to continue:"
    );
    if (entered !== null) {
      localStorage.setItem("pagewatch_token", entered.trim());
      return request(method, path, body, true);
    }
  }
  let data = {};
  try {
    data = await res.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

const enc = encodeURIComponent;

export const api = {
  status: () => request("GET", "/api/status"),
  stats: () => request("GET", "/api/stats"),
  watches: (search, tag) => {
    const params = [];
    if (search) params.push(`search=${enc(search)}`);
    if (tag) params.push(`tag=${enc(tag)}`);
    const qs = params.length ? "?" + params.join("&") : "";
    return request("GET", "/api/watches" + qs);
  },
  addWatch: (payload) => request("POST", "/api/watches", payload),
  updateWatch: (name, payload) => request("PATCH", `/api/watches/${enc(name)}`, payload),
  deleteWatch: (name) => request("DELETE", `/api/watches/${enc(name)}`),
  pauseWatch: (name) => request("POST", `/api/watches/${enc(name)}/pause`),
  resumeWatch: (name) => request("POST", `/api/watches/${enc(name)}/resume`),
  cloneWatch: (name, payload) => request("POST", `/api/watches/${enc(name)}/clone`, payload),
  checkWatch: (name, alerts = true) => request("POST", `/api/watches/${enc(name)}/check`, { alerts }),
  checkAll: (alerts = true) => request("POST", "/api/check", { alerts }),
  history: (name, limit = 50) => request("GET", `/api/watches/${enc(name)}/history?limit=${limit}`),
  deleteHistory: (name) => request("DELETE", `/api/watches/${enc(name)}/history`),
  diff: (name) => request("GET", `/api/watches/${enc(name)}/diff`),
  batchPause: (names) => request("POST", "/api/batch/pause", { names }),
  batchResume: (names) => request("POST", "/api/batch/resume", { names }),
  batchDelete: (names) => request("POST", "/api/batch/delete", { names }),
  batchCheck: (names) => request("POST", "/api/batch/check", { names }),
  config: () => request("GET", "/api/config"),
  saveConfig: (payload) => request("PUT", "/api/config", payload),
  alerts: () => request("GET", "/api/alerts"),
  addAlert: (payload) => request("POST", "/api/alerts", payload),
  updateAlert: (name, payload) => request("PATCH", `/api/alerts/${enc(name)}`, payload),
  deleteAlert: (name) => request("DELETE", `/api/alerts/${enc(name)}`),
  testAlerts: (name) => request("POST", "/api/alerts/test", name ? { name } : {}),
  alertsHistory: () => request("GET", "/api/alerts/history"),
  emailConfig: () => request("GET", "/api/alerts/email"),
  saveEmailConfig: (payload) => request("PUT", "/api/alerts/email", payload),
  exportData: () => request("GET", "/api/export"),
  importData: (payload) => request("POST", "/api/import", payload),
  daemonStart: () => request("POST", "/api/daemon/start"),
  daemonStop: () => request("POST", "/api/daemon/stop"),
  daemonStatus: () => request("GET", "/api/daemon/status"),
};