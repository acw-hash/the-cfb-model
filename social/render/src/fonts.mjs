/**
 * Font resolution: SF Pro if installed, else bundled Inter. Fail closed.
 */
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir, platform } from "node:os";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "../../..");
const interRegular = join(repoRoot, "assets/fonts/Inter-Regular.ttf");
const interMedium = join(repoRoot, "assets/fonts/Inter-Medium.ttf");
const interSemi = join(repoRoot, "assets/fonts/Inter-SemiBold.ttf");

function sfProCandidates() {
  const home = homedir();
  if (platform() === "darwin") {
    return [
      "/System/Library/Fonts/SFNS.ttf",
      "/System/Library/Fonts/SFNSDisplay.ttf",
      "/Library/Fonts/SF-Pro-Display-Regular.otf",
      `${home}/Library/Fonts/SF-Pro-Display-Regular.otf`,
    ];
  }
  if (platform() === "win32") {
    return [
      "C:/Windows/Fonts/SF-Pro-Display-Regular.otf",
      `${home}/AppData/Local/Microsoft/Windows/Fonts/SF-Pro-Display-Regular.otf`,
    ];
  }
  return [];
}

export function resolveFonts() {
  for (const path of sfProCandidates()) {
    if (existsSync(path)) {
      return {
        family: "SF Pro Display",
        faces: [
          { family: "SF Pro Display", path, weight: 400 },
          { family: "SF Pro Display", path, weight: 500 },
          { family: "SF Pro Display", path, weight: 600 },
        ],
      };
    }
  }
  if (!existsSync(interRegular)) {
    throw new Error(
      `Neither SF Pro nor bundled Inter found. Expected Inter at ${interRegular}`,
    );
  }
  const faces = [{ family: "Inter", path: interRegular, weight: 400 }];
  if (existsSync(interMedium)) {
    faces.push({ family: "Inter", path: interMedium, weight: 500 });
  }
  if (existsSync(interSemi)) {
    faces.push({ family: "Inter", path: interSemi, weight: 600 });
  }
  return { family: "Inter", faces };
}

export function fontFaceCss(fonts) {
  return fonts.faces
    .map(
      (f) => `@font-face {
  font-family: "${f.family}";
  src: url("file://${f.path.replace(/\\/g, "/")}");
  font-weight: ${f.weight};
  font-style: normal;
  font-display: block;
}`,
    )
    .join("\n");
}
