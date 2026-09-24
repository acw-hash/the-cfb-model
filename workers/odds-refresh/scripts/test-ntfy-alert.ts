/**
 * Local smoke: Worker sendAlert() with NTFY from pipeline.yaml.
 * Never prints topic/server secrets beyond ok/status.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse as parseYaml } from "yaml";
import { sendAlert } from "../src/alert";
import type { Env } from "../src/env";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const cfg = parseYaml(fs.readFileSync(path.join(root, "configs", "pipeline.yaml"), "utf8")) as {
  pipeline: { notifications: { ntfy_topic?: string; ntfy_server?: string } };
};
const topic = (cfg.pipeline.notifications.ntfy_topic ?? "").trim();
const server = (cfg.pipeline.notifications.ntfy_server ?? "https://ntfy.sh").trim();
if (!topic) throw new Error("ntfy_topic missing");

let lastStatus: number | null = null;
const fetchImpl: typeof fetch = async (input, init) => {
  const resp = await fetch(input, init);
  lastStatus = resp.status;
  return resp;
};

const env = {
  NTFY_TOPIC: topic,
  NTFY_SERVER: server,
} as Env;

await sendAlert(env, {
  title: "Ridge odds-refresh test alert",
  body: "Phase 3 Worker notifier path smoke test",
  fetchImpl,
});

console.log(JSON.stringify({ path: "worker_sendAlert", http_status: lastStatus }));
