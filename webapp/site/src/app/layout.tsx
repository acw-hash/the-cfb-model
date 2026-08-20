import "@/styles/tokens.css";
import "./globals.css";

import { FirstVisitDisclaimer } from "@/components/FirstVisitDisclaimer/FirstVisitDisclaimer";
import { FixtureBanner } from "@/components/FixtureBanner/FixtureBanner";
import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { SiteFooter } from "@/components/SiteFooter/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader/SiteHeader";
import { StalenessBanner } from "@/components/StalenessBanner/StalenessBanner";
import { loadArtifact } from "@/lib/artifacts/loader";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";
import type { MetaArtifact } from "@/lib/artifacts/types";
import { isSiteStale } from "@/lib/formatting/time";

export const metadata = {
  title: "Ridge",
  description: "College football forecasts with uncertainty",
};

/**
 * force-dynamic (PROD-500): layout always loads meta via R2 `cache: "no-store"`.
 * A static/ISR layout regenerates into that fetch and throws
 * "Page changed from static to dynamic at runtime" on every descendant route.
 * Layout-level covers `/`, `/about`, `/results`, `/game/[id]` in one place.
 */
export const dynamic = "force-dynamic";
export const revalidate = 21600;

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>): Promise<React.ReactElement> {
  let meta: MetaArtifact;
  try {
    meta = await loadArtifact<MetaArtifact>("meta");
  } catch {
    return (
      <html lang="en">
        <body>
          <MaintenanceState />
        </body>
      </html>
    );
  }

  if (!isSchemaVersionSupported(meta.schema_version)) {
    return (
      <html lang="en">
        <body>
          <MaintenanceState />
        </body>
      </html>
    );
  }

  const showFixtureBanner = meta.fixture === true;
  const showStalenessBanner = isSiteStale(meta.published_at, meta.next_expected_publish_utc);

  return (
    <html lang="en">
      <body>
        {showFixtureBanner ? <FixtureBanner /> : null}
        {showStalenessBanner ? <StalenessBanner publishedAt={meta.published_at} /> : null}
        <SiteHeader />
        <FirstVisitDisclaimer />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}
