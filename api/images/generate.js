const { requireApiKey } = require("./_lib/auth");
const { openaiFetch } = require("./_lib/openai");

function buildGeneratePayload(body) {
  const mid = String(body.model || "").trim().toLowerCase();
  const prompt = String(body.prompt || "").trim();
  const n = body.n || 1;
  const size = String(body.size || "").trim() || null;
  const quality = String(body.quality || "").trim() || null;
  const outFmt = String(body.output_format || "").trim().toLowerCase() || null;
  const kwargs = { model: String(body.model || "").trim(), prompt };

  if (mid.startsWith("dall-e-3")) {
    kwargs.n = 1;
    kwargs.response_format = "b64_json";
    kwargs.size = ["1024x1024", "1792x1024", "1024x1792"].includes(size) ? size : "1024x1024";
    if (quality === "hd" || quality === "standard") kwargs.quality = quality;
  } else if (mid.startsWith("dall-e-2")) {
    kwargs.n = Math.min(Math.max(n, 1), 10);
    kwargs.response_format = "b64_json";
    kwargs.size = ["256x256", "512x512", "1024x1024"].includes(size) ? size : "1024x1024";
  } else {
    kwargs.n = Math.min(Math.max(n, 1), 10);
    kwargs.size = ["auto", "1024x1024", "1536x1024", "1024x1536"].includes(size) ? size : "1024x1024";
    if (["standard", "low", "medium", "high", "auto"].includes(quality)) kwargs.quality = quality;
    if (["png", "jpeg", "webp"].includes(outFmt)) kwargs.output_format = outFmt;
  }
  return kwargs;
}

function packImages(resp, model, apiTag) {
  const items = (resp.data || []).map((img) => {
    const entry = {};
    if (img.b64_json) entry.b64_json = img.b64_json;
    if (img.url) entry.url = img.url;
    if (img.revised_prompt) entry.revised_prompt = img.revised_prompt;
    return entry;
  });
  return {
    images: items,
    model,
    usage: resp.usage || null,
    created: resp.created,
    api: apiTag,
    output_format: resp.output_format,
    size: resp.size,
    quality: resp.quality,
  };
}

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
  if (!body?.prompt?.trim()) {
    res.status(400).json({ message: "Нужен prompt" });
    return;
  }
  try {
    const payload = buildGeneratePayload(body);
    const resp = await openaiFetch(apiKey, "/images/generations", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    res.status(200).json(packImages(resp, payload.model, "images.generate"));
  } catch (e) {
    res.status(e.status || 502).json({ detail: { message: e.message } });
  }
};
