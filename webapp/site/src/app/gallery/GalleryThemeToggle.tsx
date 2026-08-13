"use client";

import { useEffect, useState } from "react";

import styles from "./gallery.module.css";

type ThemeChoice = "light" | "dark" | "system";

export function GalleryThemeToggle(): React.ReactElement {
  const [theme, setTheme] = useState<ThemeChoice>("system");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
      return;
    }
    root.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <div className={styles.themeToggle} role="group" aria-label="Theme">
      {(["light", "dark", "system"] as const).map((choice) => (
        <button
          key={choice}
          type="button"
          className={theme === choice ? styles.themeActive : styles.themeButton}
          onClick={() => setTheme(choice)}
        >
          {choice}
        </button>
      ))}
    </div>
  );
}
