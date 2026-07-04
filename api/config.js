module.exports = (req, res) => {
  res.status(200).json({
    deployment: "vercel",
    server_key_configured: false,
    require_client_key: true,
    server_sessions: false,
    server_billing: false,
    sync_chat: true,
    max_duration_hint_sec: 60,
    privacy_note:
      "API-ключ передаётся только в заголовке Authorization и не сохраняется на сервере Vercel.",
  });
};
