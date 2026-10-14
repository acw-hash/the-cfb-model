/**
 * Fail if pinned React / Playwright versions drift from webapp/site/package.json.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const renderPkg = JSON.parse(readFileSync(join(here, "../package.json"), "utf8"));
const webappPkg = JSON.parse(
  readFileSync(join(here, "../../../webapp/site/package.json"), "utf8"),
);

const keys = [
  ["react", "dependencies"],
  ["react-dom", "dependencies"],
  ["playwright", "dependencies"],
];

let failed = false;
for (const [name, renderSection] of keys) {
  const ours = renderPkg.dependencies?.[name] ?? renderPkg.devDependencies?.[name];
  const theirs =
    webappPkg.dependencies?.[name] ?? webappPkg.devDependencies?.[name];
  if (!ours || !theirs) {
    console.error(`Missing pin for ${name}: render=${ours} webapp=${theirs}`);
    failed = true;
    continue;
  }
  if (ours !== theirs) {
    console.error(`Version drift for ${name}: render=${ours} webapp=${theirs}`);
    failed = true;
  } else {
    console.log(`OK ${name} ${ours}`);
  }
}

if (failed) {
  process.exit(1);
}
