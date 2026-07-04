const { requireApiKey } = require("../lib/vercel/auth");
const { openaiFetch } = require("../lib/vercel/openai");
const { sendJson, methodNotAllowed } = require("../lib/vercel/http");

const FALLBACK_CHAT_MODELS = [
  "gpt-4o-mini",
  "gpt-4o",
  "gpt-5.4-mini",
  "gpt-5.5",
  "gpt-5.4",
  "gpt-5.5-pro",
  "gpt-5.4-pro",
  "gpt-4.1-mini",
  "gpt-4.1",
  "o4-mini",
  "o3-mini",
];

const EXCLUDE =
  /embed|whisper|tts|dall|moderation|realtime|transcribe|speech|audio|image|video|instruct-beta|-instruct/i;

function isLikelyChatModel(id) {
  const mid = id.toLowerCase();
  if (EXCLUDE.test(mid)) return false;
  if (mid.startsWith("gpt-") || mid.startsWith("o1") || mid.startsWith("o3") || mid.startsWith("o4")) return true;
  if (mid.startsWith("chatgpt-")) return true;
  return false;
}

module.exports = async (req, res) => {
  if (req.method !== "GET") {
    methodNotAllowed(res);
    return;
  }
  const apiKey = requireApiKey(req, res);
  if (!apiKey) return;
  try {
    const data = await openaiFetch(apiKey, "/models");
    const ids = [...new Set((data.data || []).map((m) => m.id).filter(isLikelyChatModel))].sort();
    if (!ids.length) {
      sendJson(res, 200, { models: FALLBACK_CHAT_MODELS, source: "fallback_empty" });
      return;
    }
    sendJson(res, 200, { models: ids, source: "openai" });
  } catch (e) {
    sendJson(res, 200, {
      models: FALLBACK_CHAT_MODELS,
      source: "fallback_error",
      warning: e.message,
    });
  }
};
