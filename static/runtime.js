/**
 * Dual-mode runtime: local FastAPI (server sessions) vs Vercel (client key + IndexedDB).
 * API keys in localStorage only — never written to server storage.
 */
window.AppRuntime = (function () {
  const API_KEY_KEY = "openai_local_chat_api_key";
  const DB_NAME = "openai_local_chat";
  const DB_VERSION = 1;

  let config = null;
  let dbPromise = null;

  function uuid() {
    return crypto.randomUUID ? crypto.randomUUID() : "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function now() {
    return Date.now() / 1000;
  }

  function getApiKey() {
    return (localStorage.getItem(API_KEY_KEY) || "").trim();
  }

  function setApiKey(key) {
    const k = (key || "").trim();
    if (k) localStorage.setItem(API_KEY_KEY, k);
    else localStorage.removeItem(API_KEY_KEY);
  }

  function maskKey(key) {
    if (!key || key.length < 12) return key ? "••••" : "";
    return key.slice(0, 7) + "…" + key.slice(-4);
  }

  function needsClientKey() {
    return !!(config && config.require_client_key);
  }

  function hasUsableKey() {
    if (!needsClientKey()) return true;
    return !!getApiKey();
  }

  function useClientSessions() {
    return !!(config && config.server_sessions === false);
  }

  function useSyncChat() {
    return !!(config && config.sync_chat);
  }

  function useServerBilling() {
    return !!(config && config.server_billing);
  }

  function authHeaders(extra) {
    const h = Object.assign({}, extra || {});
    if (needsClientKey()) {
      const k = getApiKey();
      if (k) h.Authorization = "Bearer " + k;
    }
    return h;
  }

  async function apiFetch(url, options) {
    const opts = Object.assign({ cache: "no-store" }, options || {});
    opts.headers = authHeaders(opts.headers || {});
    const resp = await fetch(url, opts);
    if (resp.status === 401 && needsClientKey()) {
      document.dispatchEvent(new CustomEvent("app:need-api-key"));
    }
    return resp;
  }

  async function loadConfig() {
    const r = await fetch("/api/config", { cache: "no-store" });
    config = await r.json();
    return config;
  }

  async function init() {
    await loadConfig();
    document.dispatchEvent(new CustomEvent("app:config-ready", { detail: config }));
    if (needsClientKey() && !getApiKey()) {
      document.dispatchEvent(new CustomEvent("app:need-api-key"));
    }
    return config;
  }

  function openDb() {
    if (!dbPromise) {
      dbPromise = new Promise((resolve, reject) => {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains("sessions")) {
            db.createObjectStore("sessions", { keyPath: "id" });
          }
          if (!db.objectStoreNames.contains("messages")) {
            const store = db.createObjectStore("messages", { keyPath: "id" });
            store.createIndex("session_id", "session_id", { unique: false });
          }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
    return dbPromise;
  }

  async function idbAll(storeName) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readonly");
      const store = tx.objectStore(storeName);
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }

  async function idbPut(storeName, value) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readwrite");
      tx.objectStore(storeName).put(value);
      tx.oncomplete = () => resolve(value);
      tx.onerror = () => reject(tx.error);
    });
  }

  async function idbGet(storeName, key) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readonly");
      const req = tx.objectStore(storeName).get(key);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  }

  async function idbDelete(storeName, key) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, "readwrite");
      tx.objectStore(storeName).delete(key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function listSessions(archived) {
    const all = await idbAll("sessions");
    return all
      .filter((s) => (archived ? s.archived_at : !s.archived_at))
      .sort((a, b) => (archived ? b.archived_at - a.archived_at : b.updated_at - a.updated_at))
      .slice(0, 40);
  }

  async function createSession({ title, system, model } = {}) {
    const ts = now();
    const session = {
      id: uuid(),
      title: title || "Новый чат",
      system: system || null,
      model: model || null,
      created_at: ts,
      updated_at: ts,
      archived_at: null,
    };
    await idbPut("sessions", session);
    return session;
  }

  async function getSessionMessages(sessionId) {
    const all = await idbAll("messages");
    return all
      .filter((m) => m.session_id === sessionId)
      .sort((a, b) => a.created_at - b.created_at);
  }

  async function getSessionBundle(sessionId) {
    const session = await idbGet("sessions", sessionId);
    if (!session) return null;
    const messages = await getSessionMessages(sessionId);
    return { session, messages };
  }

  async function touchSession(sessionId, { title, system, model } = {}) {
    const session = await idbGet("sessions", sessionId);
    if (!session) return null;
    session.updated_at = now();
    if (title != null) session.title = title;
    if (system !== undefined) session.system = system;
    if (model !== undefined) session.model = model;
    await idbPut("sessions", session);
    return session;
  }

  async function archiveSession(sessionId) {
    const session = await idbGet("sessions", sessionId);
    if (!session) return null;
    session.archived_at = now();
    session.updated_at = now();
    await idbPut("sessions", session);
    return session;
  }

  async function deleteSession(sessionId) {
    const session = await idbGet("sessions", sessionId);
    if (!session?.archived_at) return false;
    await idbDelete("sessions", sessionId);
    const all = await idbAll("messages");
    await Promise.all(
      all.filter((m) => m.session_id === sessionId).map((m) => idbDelete("messages", m.id))
    );
    return true;
  }

  async function appendMessage(msg) {
    await idbPut("messages", msg);
    return msg;
  }

  async function updateMessage(msg) {
    await idbPut("messages", msg);
    return msg;
  }

  async function sendChatSync(payload) {
    const r = await apiFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const err = new Error(data.detail?.message || data.message || "Ошибка чата");
      err.detail = data.detail || data;
      err.status = r.status;
      throw err;
    }
    return data;
  }

  return {
    init,
    loadConfig,
    getConfig: () => config,
    getApiKey,
    setApiKey,
    maskKey,
    needsClientKey,
    hasUsableKey,
    useClientSessions,
    useSyncChat,
    useServerBilling,
    authHeaders,
    apiFetch,
    listSessions,
    createSession,
    getSessionBundle,
    getSessionMessages,
    touchSession,
    archiveSession,
    deleteSession,
    appendMessage,
    updateMessage,
    sendChatSync,
    uuid,
    now,
  };
})();
