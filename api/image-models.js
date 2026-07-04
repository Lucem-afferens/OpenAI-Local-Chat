const { requireApiKey } = require("../lib/vercel/auth");
const { openaiFetch } = require("../lib/vercel/openai");
const { sendJson, methodNotAllowed } = require("../lib/vercel/http");

const FALLBACK_IMAGE_MODELS = [
  "gpt-image-2",
  "gpt-image-1.5",
  "gpt-image-1",
  "gpt-image-1-mini",
  "dall-e-3",
  "dall-e-2",
];

function isImageModel(id) {
  const mid = id.toLowerCase();
  if (mid.includes("gpt-image") || mid.includes("chatgpt-image")) return true;
  return mid.startsWith("dall-e");
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
    const ids = [...new Set((data.data || []).map((m) => m.id).filter(isImageModel))].sort();
    if (!ids.length) {
      sendJson(res, 200, { models: FALLBACK_IMAGE_MODELS, source: "fallback_empty" });
      return;
    }
    sendJson(res, 200, { models: ids, source: "openai" });
  } catch (e) {
    sendJson(res, 200, {
      models: FALLBACK_IMAGE_MODELS,
      source: "fallback_error",
      warning: e.message,
    });
  }
};
