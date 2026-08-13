const ALLOWED_PATHS = [
  /[\\/]lib[\\/]formatting[\\/]/,
  /[\\/]components[\\/]Figure[\\/]/,
  /[\\/]tests[\\/]/,
];

/** @type {import('eslint').Rule.RuleModule} */
const requireFigureForNumbers = {
  meta: {
    type: "problem",
    docs: {
      description: "Require numeric UI output via Figure/formatting modules (§4.2 tabular guard)",
    },
    schema: [],
  },
  create(context) {
    const filename = context.filename.replace(/\\/g, "/");
    if (!filename.includes("/src/components/") && !filename.includes("/src/app/")) {
      return {};
    }
    if (ALLOWED_PATHS.some((re) => re.test(filename))) {
      return {};
    }

    return {
      CallExpression(node) {
        if (
          node.callee.type === "MemberExpression" &&
          node.callee.property.type === "Identifier" &&
          node.callee.property.name === "toFixed"
        ) {
          context.report({
            node,
            message:
              "Do not call toFixed in UI components; use lib/formatting/numbers and render via <Figure>.",
          });
        }
      },
    };
  },
};

module.exports = {
  rules: {
    "require-figure-for-numbers": requireFigureForNumbers,
  },
};
