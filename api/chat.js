const { requireApiKey } = require("./_lib/auth");
const { executeChatWithRouting, httpError } = require("./_lib/chat");

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ message: "Method not allowed" });
    return;
  }
  const apiKey = requireApiKey(req, res);
  if (!apiKey) return;

  let body;
  try {
    body = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
  } catch {
    res.status(400).json({ message: "Invalid JSON" });
    return;
  }
  if (!body?.message?.trim() || !body?.model?.trim()) {
    res.status(400).json({ message: "Нужны поля model и message" });
    return;
  }

  try {
    const outcome = await executeChatWithRouting(apiKey, body);
    if (outcome.ok) {
      res.status(200).json(outcome.data);
      return;
    }
    const err = outcome.error || { kind: "unknown", message: "Неизвестная ошибка." };
    const status = err.kind === "timeout" ? 504 : err.kind === "rate_limit" ? 429 : 502;
    res.status(status).json({ detail: err });
  } catch (e) {
    const { status, detail } = httpError(e);
    res.status(status).json({ detail });
  }
};
