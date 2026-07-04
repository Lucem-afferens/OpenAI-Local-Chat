const Busboy = require("busboy");
const { requireApiKey } = require("../_lib/auth");
const { OPENAI_BASE } = require("../_lib/openai");
const { sendJson, methodNotAllowed } = require("../_lib/http");

function parseMultipart(req) {
  return new Promise((resolve, reject) => {
    const fields = {};
    const files = {};
    const bb = Busboy({ headers: req.headers });
    bb.on("file", (name, stream, info) => {
      const chunks = [];
      stream.on("data", (d) => chunks.push(d));
      stream.on("end", () => {
        files[name] = { buffer: Buffer.concat(chunks), filename: info.filename, mimeType: info.mimeType };
      });
    });
    bb.on("field", (name, val) => {
      fields[name] = val;
    });
    bb.on("error", reject);
    bb.on("finish", () => resolve({ fields, files }));
    req.pipe(bb);
  });
}

async function handler(req, res) {
  if (req.method !== "POST") {
    methodNotAllowed(res);
    return;
  }
  const apiKey = requireApiKey(req, res);
  if (!apiKey) return;

  try {
    const { fields, files } = await parseMultipart(req);
    const prompt = String(fields.prompt || "").trim();
    const model = String(fields.model || "gpt-image-1.5").trim();
    if (!prompt || !files.image) {
      sendJson(res, 400, { message: "Нужны prompt и image" });
      return;
    }

    const form = new FormData();
    form.append("prompt", prompt);
    form.append("model", model);
    form.append("n", String(Math.min(Math.max(parseInt(fields.n, 10) || 1, 1), 10)));
    const mid = model.toLowerCase();
    const size = String(fields.size || "").trim();
    const quality = String(fields.quality || "auto").trim();
    if (mid.startsWith("dall-e-2")) {
      form.append("response_format", "b64_json");
      form.append("size", ["256x256", "512x512", "1024x1024"].includes(size) ? size : "1024x1024");
    } else {
      form.append("size", ["auto", "1024x1024", "1536x1024", "1024x1536"].includes(size) ? size : "auto");
      if (["standard", "low", "medium", "high", "auto"].includes(quality)) form.append("quality", quality);
      const of = String(fields.output_format || "").trim().toLowerCase();
      if (["png", "jpeg", "webp"].includes(of)) form.append("output_format", of);
      if (mid.includes("gpt-image") && !mid.includes("mini") && ["high", "low"].includes(fields.input_fidelity)) {
        form.append("input_fidelity", fields.input_fidelity);
      }
    }

    const imgBlob = new Blob([files.image.buffer], { type: files.image.mimeType || "application/octet-stream" });
    form.append("image", imgBlob, files.image.filename || "input.png");
    if (files.mask?.buffer?.length) {
      const maskBlob = new Blob([files.mask.buffer], { type: files.mask.mimeType || "application/octet-stream" });
      form.append("mask", maskBlob, files.mask.filename || "mask.png");
    }

    const resp = await fetch(`${OPENAI_BASE}/images/edits`, {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}` },
      body: form,
    });
    const data = await resp.json();
    if (!resp.ok) {
      sendJson(res, resp.status, { detail: { message: data?.error?.message || "OpenAI error" } });
      return;
    }
    const items = (data.data || []).map((img) => {
      const entry = {};
      if (img.b64_json) entry.b64_json = img.b64_json;
      if (img.url) entry.url = img.url;
      return entry;
    });
    sendJson(res, 200, {
      images: items,
      model,
      usage: data.usage || null,
      created: data.created,
      api: "images.edit",
    });
  } catch (e) {
    sendJson(res, 502, { detail: { message: e.message || "Upload failed" } });
  }
}

handler.config = { api: { bodyParser: false } };
module.exports = handler;
