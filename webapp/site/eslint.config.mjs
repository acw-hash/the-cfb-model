import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { FlatCompat } from "@eslint/eslintrc";
import ridgePlugin from "eslint-plugin-ridge";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    plugins: {
      ridge: ridgePlugin,
    },
    rules: {
      "ridge/require-figure-for-numbers": "error",
    },
  },
];

export default eslintConfig;
