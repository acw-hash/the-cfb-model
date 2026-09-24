import type { Env } from "./env";

export async function sendAlert(
  env: Env,
  opts: {
    title: string;
    body: string;
    fetchImpl?: typeof fetch;
  },
): Promise<void> {
  const { title, body } = opts;
  const fetchImpl = opts.fetchImpl ?? fetch;
  const errors: string[] = [];
  const topic = env.NTFY_TOPIC?.trim();
  if (topic) {
    try {
      const server = (env.NTFY_SERVER?.trim() || "https://ntfy.sh").replace(/\/$/, "");
      const headers: Record<string, string> = {
        Title: title.slice(0, 250),
        Priority: "4",
        Tags: "odds_refresh",
      };
      const token = env.NTFY_AUTH_TOKEN?.trim();
      if (token) headers.Authorization = `Bearer ${token}`;
      const resp = await fetchImpl(`${server}/${topic}`, {
        method: "POST",
        headers,
        body,
      });
      if (!resp.ok) errors.push(`ntfy HTTP ${resp.status}`);
    } catch (e) {
      errors.push(`ntfy ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  const bot = env.TELEGRAM_BOT_TOKEN?.trim();
  const chat = env.TELEGRAM_CHAT_ID?.trim();
  if (bot && chat) {
    try {
      const resp = await fetchImpl(`https://api.telegram.org/bot${bot}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chat,
          text: `${title}\n${body}`.slice(0, 4000),
        }),
      });
      if (!resp.ok) errors.push(`telegram HTTP ${resp.status}`);
    } catch (e) {
      errors.push(`telegram ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  if (!topic && !(bot && chat)) {
    console.warn("alert_suppressed_no_notifier", title, body.slice(0, 200));
  } else if (errors.length) {
    console.error("alert_partial_failure", errors.join("; "));
  }
}
