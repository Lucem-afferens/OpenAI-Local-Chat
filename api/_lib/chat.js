const { openaiFetch } = require("./openai");

const DEFAULT_CHAT_MODEL = "gpt-4o-mini";
const CLASSIFIER_MODEL = DEFAULT_CHAT_MODEL;

const TIER_MODEL_PRIORITY = {
  simple: ["gpt-4o-mini", "gpt-5.4-mini", "gpt-4.1-mini", "gpt-4o-mini-2024-07-18"],
  complex: ["gpt-5.5", "gpt-4o", "gpt-5.4", "gpt-4.1", "gpt-4.1-mini"],
  reasoning: ["gpt-5.5-pro", "gpt-5.4-pro", "o4-mini", "o3-mini", "gpt-5.5", "gpt-4o"],
};

const REPLY_LANGUAGE_INSTRUCTIONS = {
  ru: "Всегда отвечай на русском языке. Если пользователь явно просит другой язык — используй его.",
  en: "Always respond in English. If the user explicitly asks for another language — use that language.",
};

function effectiveSystem(body) {
  const lang = (body.reply_language || "ru").toLowerCase();
  const langInst = REPLY_LANGUAGE_INSTRUCTIONS[lang] || REPLY_LANGUAGE_INSTRUCTIONS.ru;
  const custom = (body.system || "").trim();
  return custom ? `${langInst}\n\n${custom}` : langInst;
}

function buildUserMessage(message, attachmentName, attachmentText) {
  const msg = String(message || "").trim();
  const doc = String(attachmentText || "").trim();
  if (!doc) return msg;
  const name = (attachmentName || "document").trim();
  return `Ниже приложен файл «${name}» для изучения.\n\n---\n${doc}\n---\n\nЗапрос пользователя:\n${msg}`;
}

function preferResponsesApi(modelId) {
  const mid = modelId.toLowerCase();
  if (mid.includes("-instruct")) return false;
  if (!mid.includes("-pro")) return false;
  return mid.startsWith("gpt-") || mid.startsWith("o1") || mid.startsWith("o3");
}

function pickModelForTier(tier, allowPro) {
  if (tier === "reasoning" && !allowPro) tier = "complex";
  const list = TIER_MODEL_PRIORITY[tier] || TIER_MODEL_PRIORITY.simple;
  return list[0];
}

function parseClassifierTier(raw) {
  const low = String(raw || "").trim().toLowerCase();
  if (low.includes("reason")) return "reasoning";
  if (low.includes("simple") || low === "s") return "simple";
  if (low.includes("complex")) return "complex";
  return "complex";
}

async function classifyRequestTier(apiKey, body) {
  if (body.attachment_text && body.attachment_text.trim().length > 48000) {
    return { tier: "complex", raw: "large_attachment", source: "heuristic" };
  }
  const preview = String(body.message || "").trim().slice(0, 3000);
  let attachmentNote = "";
  if (body.attachment_name) attachmentNote = `\n[Прикреплён файл: ${body.attachment_name}]`;
  const system =
    "Классифицируй сложность запроса. Ответь одним словом: simple, complex или reasoning.\n" +
    "simple — короткий вопрос, перевод, черновик, факт\n" +
    "complex — код, анализ, сравнение, структурированный ответ, разбор файла\n" +
    "reasoning — математика, доказательства, глубокий дебаг, архитектура, многошаговая логика";
  try {
    const data = await openaiFetch(apiKey, "/chat/completions", {
      method: "POST",
      body: JSON.stringify({
        model: CLASSIFIER_MODEL,
        messages: [
          { role: "system", content: system },
          { role: "user", content: `Запрос:\n${preview}${attachmentNote}` },
        ],
        max_tokens: 16,
        temperature: 0,
      }),
    });
    const raw = data?.choices?.[0]?.message?.content?.trim() || "";
    return { tier: parseClassifierTier(raw), raw, source: "classifier" };
  } catch {
    return { tier: "complex", raw: "classifier_failed", source: "fallback" };
  }
}

async function resolveRoutedBody(apiKey, body) {
  const mode = String(body.routing_mode || "manual").toLowerCase();
  const meta = { routing_mode: mode };
  if (mode !== "auto") return { body, meta };

  const classified = await classifyRequestTier(apiKey, body);
  let tier = classified.tier;
  let tierDowngraded = false;
  if (tier === "reasoning" && !body.allow_pro) {
    tier = "complex";
    tierDowngraded = true;
  }
  const model = pickModelForTier(tier, body.allow_pro);
  meta.routing_mode = "auto";
  meta.routing_tier = tier;
  meta.routing_classifier = classified.raw;
  meta.routing_classifier_source = classified.source;
  meta.routing_model_planned = model;
  if (tierDowngraded) meta.routing_tier_downgraded = true;
  return { body: { ...body, model }, meta };
}

function textFromResponses(resp) {
  if (resp?.output_text?.trim()) return resp.output_text.trim();
  const chunks = [];
  for (const item of resp?.output || []) {
    if (item.type !== "message") continue;
    for (const part of item.content || []) {
      if (part.type === "output_text") chunks.push(part.text || "");
    }
  }
  return chunks.join("").trim();
}

async function responsesApi(apiKey, body) {
  const model = String(body.model || "").trim();
  const kwargs = {
    model,
    input: buildUserMessage(body.message, body.attachment_name, body.attachment_text),
  };
  const system = effectiveSystem(body);
  if (system) kwargs.instructions = system;
  const resp = await openaiFetch(apiKey, "/responses", {
    method: "POST",
    body: JSON.stringify(kwargs),
  });
  return {
    reply: textFromResponses(resp),
    model: resp.model || model,
    finish_reason: null,
    usage: resp.usage || null,
    api: "responses",
    response_status: resp.status,
    streamed: false,
  };
}

async function chatCompletions(apiKey, body) {
  const model = String(body.model || "").trim();
  const messages = [];
  const system = effectiveSystem(body);
  if (system) messages.push({ role: "system", content: system });
  messages.push({
    role: "user",
    content: buildUserMessage(body.message, body.attachment_name, body.attachment_text),
  });
  const resp = await openaiFetch(apiKey, "/chat/completions", {
    method: "POST",
    body: JSON.stringify({ model, messages }),
  });
  const choice = resp.choices?.[0];
  return {
    reply: choice?.message?.content || "",
    model: resp.model || model,
    finish_reason: choice?.finish_reason || null,
    usage: resp.usage || null,
    api: "chat.completions",
  };
}

async function executeChatSync(apiKey, body) {
  const model = String(body.model || "").trim();
  if (preferResponsesApi(model)) {
    return { ok: true, data: await responsesApi(apiKey, body) };
  }
  try {
    return { ok: true, data: await chatCompletions(apiKey, body) };
  } catch (e) {
    const err = String(e.message || "").toLowerCase();
    if (err.includes("not a chat model") || err.includes("v1/completions")) {
      try {
        return { ok: true, data: await responsesApi(apiKey, body) };
      } catch (e2) {
        return {
          ok: false,
          error: { kind: "routing_failed", message: e2.message || "Модель недоступна." },
        };
      }
    }
    throw e;
  }
}

function attachRouting(data, meta, fallback = false) {
  if (meta.routing_mode !== "auto") return data;
  return {
    ...data,
    routing_mode: "auto",
    routing_tier: meta.routing_tier,
    routing_classifier: meta.routing_classifier,
    ...(meta.routing_tier_downgraded ? { routing_tier_downgraded: true } : {}),
    ...(fallback ? { routing_fallback: true } : {}),
  };
}

async function executeChatWithRouting(apiKey, body) {
  const { body: execBody, meta } = await resolveRoutedBody(apiKey, body);
  let outcome = await executeChatSync(apiKey, execBody);
  if (outcome.ok) {
    outcome.data = attachRouting(outcome.data, meta);
    return outcome;
  }
  if (meta.routing_tier !== "simple") return outcome;
  const complexModel = pickModelForTier("complex", false);
  if (complexModel === execBody.model) return outcome;
  const retry = await executeChatSync(apiKey, { ...execBody, model: complexModel });
  if (retry.ok) {
    retry.data = attachRouting(retry.data, { ...meta, routing_tier: "complex" }, true);
    return retry;
  }
  return outcome;
}

function httpError(err) {
  const status = err.status || 502;
  return {
    status,
    detail: {
      kind: status === 429 ? "rate_limit" : status === 401 ? "auth" : "http",
      message: err.message || "Ошибка OpenAI",
    },
  };
}

module.exports = { executeChatWithRouting, httpError };
