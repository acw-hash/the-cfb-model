"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import styles from "./SiteHeader.module.css";

const LINKS = [
  { href: "/", label: "This Week", match: (p: string) => p === "/" },
  {
    href: "/results",
    label: "Results",
    match: (p: string) => p === "/results" || p.startsWith("/results/"),
  },
  {
    href: "/about",
    label: "About",
    match: (p: string) => p === "/about" || p.startsWith("/about/"),
  },
] as const;

/**
 * Persistent minimal chrome (§4 restraint): Ridge wordmark + three page links.
 * Canonical hrefs only — page-local query state on Results or This Week is not preserved.
 */
export function SiteHeader(): React.ReactElement {
  const pathname = usePathname() ?? "/";

  return (
    <header className={styles.header} data-testid="site-header">
      <div className={styles.inner}>
        <Link href="/" className={styles.wordmark} data-testid="site-wordmark">
          Ridge
        </Link>
        <nav aria-label="Site">
          <ul className={styles.nav}>
            {LINKS.map((link) => {
              const current = link.match(pathname);
              return (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    aria-current={current ? "page" : undefined}
                    data-testid={`nav-${link.label.toLowerCase().replace(/\s+/g, "-")}`}
                  >
                    {link.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
