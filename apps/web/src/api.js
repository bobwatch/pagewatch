async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
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
  watches: () => request("GET", "/api/watches"),
  addWatch: (payload) => request("POST", "/api/watches", payload),
  updateWatch: (name, payload) => request("PATCH", `/api/watches/${enc(name)}`, payload),
  deleteWatch: (name) => request("DELETE", `/api/watches/${enc(name)}`),
  checkWatch: (name) => request("POST", `/api/watches/${enc(name)}/check`, {}),
  checkAll: () => request("POST", "/api/check", {}),
  history: (name, limit = 50) => request("GET", `/api/watches/${enc(name)}/history?limit=${limit}`),
  diff: (name) => request("GET", `/api/watches/${enc(name)}/diff`),
  config: () => request("GET", "/api/config"),
  saveConfig: (payload) => request("PUT", "/api/config", payload),
  alerts: () => request("GET", "/api/alerts"),
  addAlert: (payload) => request("POST", "/api/alerts", payload),
  deleteAlert: (name) => request("DELETE", `/api/alerts/${enc(name)}`),
  testAlerts: (name) => request("POST", "/api/alerts/test", name ? { name } : {}),
};
