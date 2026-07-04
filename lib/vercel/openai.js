const OPENAI_BASE = "https://api.openai.com/v1";

async function openaiFetch(apiKey, path, options = {}) {
  const url = path.startsWith("http") ? path : `${OPENAI_BASE}${path}`;
  const headers = {
    Authorization: `Bearer ${apiKey}`,
    ...(options.headers || {}),
  };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(url, {
    method: options.method || "GET",
    headers,
    body: options.body,
    signal: options.signal,
  });
  const text = await resp.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!resp.ok) {
    const msg =
      data?.error?.message ||
      data?.message ||
      text.slice(0, 400) ||
      `OpenAI HTTP ${resp.status}`;
    const err = new Error(msg);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

module.exports = { openaiFetch, OPENAI_BASE };
