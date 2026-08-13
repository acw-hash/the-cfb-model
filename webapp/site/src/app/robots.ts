import type { MetadataRoute } from "next";

/** Private preview: disallow all crawlers. Public launch is W8. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      disallow: "/",
    },
  };
}
