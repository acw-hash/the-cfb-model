/**
 * Private R2 object fetch via S3-compatible SigV4 (server-side only).
 *
 * Private-preview default: bucket is NOT world-readable. The site holds a
 * read-only R2 API token in Vercel server env — never the workstation write key.
 */

import { createHash, createHmac } from "node:crypto";

export interface R2FetchConfig {
  bucket: string;
  endpointUrl: string;
  accessKeyId: string;
  secretAccessKey: string;
  region?: string;
}

function requireEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`Missing required env ${name} for private R2 artifact fetch`);
  }
  return value;
}

/** Resolve R2 config from server env. Throws when incomplete. */
export function resolveR2Config(): R2FetchConfig {
  const bucket = requireEnv("R2_BUCKET");
  const accessKeyId = requireEnv("R2_ACCESS_KEY_ID");
  const secretAccessKey = requireEnv("R2_SECRET_ACCESS_KEY");
  const endpointUrl = (
    process.env.R2_ENDPOINT_URL?.trim() ||
    (process.env.R2_ACCOUNT_ID?.trim()
      ? `https://${process.env.R2_ACCOUNT_ID.trim()}.r2.cloudflarestorage.com`
      : "")
  ).replace(/\/$/, "");
  if (!endpointUrl) {
    throw new Error("Set R2_ENDPOINT_URL or R2_ACCOUNT_ID for private R2 artifact fetch");
  }
  return {
    bucket,
    endpointUrl,
    accessKeyId,
    secretAccessKey,
    region: process.env.R2_REGION?.trim() || "auto",
  };
}

function sha256Hex(data: string | Buffer): string {
  return createHash("sha256").update(data).digest("hex");
}

function hmac(key: Buffer | string, data: string): Buffer {
  return createHmac("sha256", key).update(data, "utf8").digest();
}

function amzDate(now: Date): { amzDate: string; dateStamp: string } {
  const iso = now
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}Z$/, "Z");
  return { amzDate: iso, dateStamp: iso.slice(0, 8) };
}

/** Sign a GET for an R2 object key under `latest/`. */
export function signedR2GetUrl(
  key: string,
  config: R2FetchConfig,
  now: Date = new Date(),
): {
  url: string;
  headers: Record<string, string>;
} {
  const region = config.region ?? "auto";
  const service = "s3";
  const method = "GET";
  const { amzDate: amz, dateStamp } = amzDate(now);
  const host = new URL(config.endpointUrl).host;
  const canonicalUri = `/${config.bucket}/${key.split("/").map(encodeURIComponent).join("/")}`;
  const payloadHash = sha256Hex("");
  const canonicalHeaders =
    `host:${host}\n` + `x-amz-content-sha256:${payloadHash}\n` + `x-amz-date:${amz}\n`;
  const signedHeaders = "host;x-amz-content-sha256;x-amz-date";
  const canonicalRequest = [
    method,
    canonicalUri,
    "",
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join("\n");
  const credentialScope = `${dateStamp}/${region}/${service}/aws4_request`;
  const stringToSign = ["AWS4-HMAC-SHA256", amz, credentialScope, sha256Hex(canonicalRequest)].join(
    "\n",
  );
  const kDate = hmac(`AWS4${config.secretAccessKey}`, dateStamp);
  const kRegion = hmac(kDate, region);
  const kService = hmac(kRegion, service);
  const kSigning = hmac(kService, "aws4_request");
  const signature = createHmac("sha256", kSigning).update(stringToSign, "utf8").digest("hex");
  const authorization =
    `AWS4-HMAC-SHA256 Credential=${config.accessKeyId}/${credentialScope}, ` +
    `SignedHeaders=${signedHeaders}, Signature=${signature}`;

  return {
    url: `${config.endpointUrl}${canonicalUri}`,
    headers: {
      Authorization: authorization,
      "x-amz-content-sha256": payloadHash,
      "x-amz-date": amz,
    },
  };
}

/** Fetch a JSON object from private R2. */
export async function fetchR2Json<T>(key: string, config?: R2FetchConfig): Promise<T> {
  const cfg = config ?? resolveR2Config();
  const { url, headers } = signedR2GetUrl(key, cfg);
  const response = await fetch(url, {
    headers,
    // Private objects — do not use Next data cache keyed on a public URL alone.
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`R2 GET ${key} failed: HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}
