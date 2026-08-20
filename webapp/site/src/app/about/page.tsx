import { AboutPage } from "@/components/About/AboutPage";

/**
 * Dynamic via root layout (PROD-500). About itself has no page-level artifact
 * fetch, but layout's no-store meta load still forces regeneration into dynamic.
 */
export const revalidate = 21600;

export const metadata = {
  title: "About — Ridge",
  description: "How Ridge forecasts work, data honesty, disclaimers, and responsible gambling",
};

/**
 * Methodology / About (§5.4, §6).
 * No artifact load required beyond layout meta — copy is specification-backed.
 */
export default function AboutRoute(): React.ReactElement {
  const year = new Date().getUTCFullYear();
  return <AboutPage year={year} />;
}
