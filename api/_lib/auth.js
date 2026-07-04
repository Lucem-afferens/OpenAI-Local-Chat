/** Читает ключ клиента из заголовков. Не логировать и не сохранять. */

function readApiKey(req) {
  const auth = String(req.headers.authorization || "");
  if (auth.toLowerCase().startsWith("bearer ")) {
    const key = auth.slice(7).trim();
    if (key) return key;
  }
  const header = req.headers["x-openai-api-key"];
  if (header && String(header).trim()) return String(header).trim();
  return null;
}

function missingKeyResponse(res) {
  res.status(401).json({
    kind: "missing_api_key",
    message:
      "Укажите API-ключ OpenAI в настройках. Ключ хранится только в вашем браузере и передаётся в заголовке запроса — мы его не сохраняем.",
  });
}

function requireApiKey(req, res) {
  const key = readApiKey(req);
  if (!key) {
    missingKeyResponse(res);
    return null;
  }
  return key;
}

module.exports = { readApiKey, requireApiKey, missingKeyResponse };
