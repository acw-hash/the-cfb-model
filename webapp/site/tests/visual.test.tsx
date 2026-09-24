/**
 * @vitest-environment happy-dom
 *
 * Vitest on Vercel Linux breaks ESM named/namespace access to react.act
 * (keys list `act` but the value is undefined; the real fn is on `.default`
 * or the CJS module). Prefer act over flushSync so useEffect (URL ?step=)
 * is flushed.
 */
import { createRequire } from "node:module";
import * as ReactNS from "react";
import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ModelWalkthrough from "@/components/ModelWalkthrough/ModelWalkthrough";
import { PAGE, STEPS } from "@/lib/visual/copy";

type ActFn = (callback: () => void) => void;

function resolveAct(): ActFn {
  // Prefer CJS: Vitest ESM interop on Vercel Linux lists `act` on the namespace
  // but the binding is undefined; the real function is on the CJS export.
  const cjs = createRequire(import.meta.url)("react") as { act?: unknown };
  if (typeof cjs.act === "function") {
    return cjs.act as ActFn;
  }
  const ns = ReactNS as typeof ReactNS & {
    act?: unknown;
    default?: { act?: unknown };
  };
  for (const candidate of [ns.act, ns.default?.act]) {
    if (typeof candidate === "function") {
      return candidate as ActFn;
    }
  }
  throw new Error(
    `Could not resolve react.act (cjs=${typeof cjs.act}, ns=${typeof ns.act}, default=${typeof ns.default?.act}). ` +
      `NS keys: ${Object.keys(ns).sort().join(", ")}`,
  );
}

const act = resolveAct();

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: ReactNode;
    className?: string;
  }) => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

function setStepQuery(step: number | null): void {
  const path = step === null ? "/visual" : `/visual?step=${step}`;
  window.history.replaceState({}, "", path);
}

describe("/visual ModelWalkthrough", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    setStepQuery(null);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  function renderWalkthrough(): void {
    act(() => {
      root.render(<ModelWalkthrough />);
    });
  }

  function stepTitle(): string {
    const h2 = container.querySelector("h2");
    expect(h2, "expected step title").not.toBeNull();
    return h2?.textContent ?? "";
  }

  function clickButton(label: string): void {
    const buttons = [...container.querySelectorAll("button")];
    const btn = buttons.find((b) => b.textContent?.trim() === label);
    expect(btn, `expected button "${label}"`).toBeDefined();
    act(() => {
      btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  function pressKey(key: string): void {
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    });
  }

  it("renders step 1 by default", () => {
    renderWalkthrough();
    expect(stepTitle()).toBe(STEPS[0].title);
    expect(container.textContent).toContain(PAGE.stepOf(1, STEPS.length));
    expect(container.textContent).toContain(PAGE.exampleTag);
  });

  it("opens step 4 when ?step=4 is in the URL", () => {
    setStepQuery(4);
    renderWalkthrough();
    expect(stepTitle()).toBe(STEPS[3].title);
    expect(container.textContent).toContain(PAGE.stepOf(4, STEPS.length));
  });

  it("Next and Back change steps", () => {
    renderWalkthrough();
    clickButton(PAGE.next);
    expect(stepTitle()).toBe(STEPS[1].title);
    clickButton(PAGE.back);
    expect(stepTitle()).toBe(STEPS[0].title);
  });

  it("arrow keys change steps", () => {
    renderWalkthrough();
    pressKey("ArrowRight");
    expect(stepTitle()).toBe(STEPS[1].title);
    pressKey("ArrowLeft");
    expect(stepTitle()).toBe(STEPS[0].title);
  });

  it("last step button reads Start over and returns to step 1", () => {
    setStepQuery(STEPS.length);
    renderWalkthrough();
    expect(stepTitle()).toBe(STEPS[STEPS.length - 1].title);
    const restart = [...container.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === PAGE.restart,
    );
    expect(restart, "expected Start over").toBeDefined();
    act(() => {
      restart!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(stepTitle()).toBe(STEPS[0].title);
  });

  it("shows Illustrative example on every step", () => {
    renderWalkthrough();
    for (let i = 0; i < STEPS.length; i++) {
      if (i > 0) {
        clickButton(PAGE.next);
      }
      expect(container.textContent).toContain(PAGE.exampleTag);
      expect(stepTitle()).toBe(STEPS[i].title);
    }
  });

  it("step 4 with both models off shows Forecast unavailable", () => {
    setStepQuery(4);
    renderWalkthrough();
    expect(stepTitle()).toBe(STEPS[3].title);
    // Ensemble member toggles use aria-pressed; spine step buttons do not.
    const modelButtons = [...container.querySelectorAll("button[aria-pressed]")];
    expect(modelButtons.length).toBeGreaterThanOrEqual(2);
    for (const btn of modelButtons) {
      if (btn.getAttribute("aria-pressed") === "true") {
        act(() => {
          btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
      }
    }
    expect(container.textContent).toContain("Forecast unavailable");
  });
});
