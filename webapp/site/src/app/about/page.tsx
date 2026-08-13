import { AboutPage } from "@/components/About/AboutPage";

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
