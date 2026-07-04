/** JSON-ответ совместимый с @vercel/node и plain Node. */

function sendJson(res, status, payload) {
  if (typeof res.status === "function" && typeof res.json === "function") {
    res.status(status).json(payload);
    return;
  }
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(payload));
}

function methodNotAllowed(res) {
  sendJson(res, 405, { message: "Method not allowed" });
}

module.exports = { sendJson, methodNotAllowed };
