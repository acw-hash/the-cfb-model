import { notFound } from "next/navigation";

/**
 * Gallery routes render synthetic / doctored demo states. They must not be
 * reachable on production builds (NODE_ENV=production), including Vercel
 * production and `next start`. Local `next dev` keeps NODE_ENV=development.
 */
export function isGalleryEnabled(): boolean {
  return process.env.NODE_ENV !== "production";
}

/** Call at the top of every gallery page — returns never on production. */
export function assertGalleryAllowed(): void {
  if (!isGalleryEnabled()) {
    notFound();
  }
}
