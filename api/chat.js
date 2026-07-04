const { requireApiKey } = require("../lib/vercel/auth");
const { executeChatWithRouting, httpError } = require("../lib/vercel/chat");
const { sendJson, methodNotAllowed } = require("../lib/vercel/http");

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    methodNotAllowed(res);
    return;
  }
  const apiKey = requireApiKey(req, res);
  if (!apiKey) return;

  let body;
  try {
    body = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
  } catch {
    sendJson(res, 400, { message: "Invalid JSON" });
    return;
  }
  if (!body?.message?.trim() || !body?.model?.trim()) {
    sendJson(res, 400, { message: "Нужны поля model и message" });
    return;
  }

  try {
    const outcome = await executeChatWithRouting(apiKey, body);
    if (outcome.ok) {
      sendJson(res, 200, outcome.data);
      return;
    }
    const err = outcome.error || { kind: "unknown", message: "Неизвестная ошибка." };
    const status = err.kind === "timeout" ? 504 : err.kind === "rate_limit" ? 429 : 502;
    sendJson(res, status, { detail: err });
  } catch (e) {
    const { status, detail } = httpError(e);
    sendJson(res, status, { detail });
  }
};
