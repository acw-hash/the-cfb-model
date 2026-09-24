/**
 * @vitest-environment happy-dom
 *
 * Do not import react.act or createRequire — both break under Vitest on Vercel
 * Linux (undefined act bindings / createRequire not a function). Use flushSync
 * from react-dom to flush setState from native listeners, and a macrotask
 * settle() so useEffect (URL ?step=) runs after render.
 */
import type { ReactNode } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ModelWalkthrough from "@/components/ModelWalkthrough/ModelWalkthrough";
import { PAGE, STEPS } from "@/lib/visual/copy";

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

/** Yield so React useEffect runs under happy-dom (flushSync does not run effects). */
async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise<void>((resolve) => {
    setTimeout(resolve, 0);
  });
}

async function waitFor(predicate: () => boolean, label: string, timeoutMs = 2000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (predicate()) {
      return;
    }
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 10);
    });
  }
  throw new Error(`waitFor timed out: ${label}`);
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
    flushSync(() => {
      root.unmount();
    });
    container.remove();
  });

  async function renderWalkthrough(): Promise<void> {
    flushSync(() => {
      root.render(<ModelWalkthrough />);
    });
    await settle();
  }

  function stepTitle(): string {
    const h2 = container.querySelector("h2");
    expect(h2, "expected step title").not.toBeNull();
    return h2?.textContent ?? "";
  }

  async function clickButton(label: string): Promise<void> {
    const buttons = [...container.querySelectorAll("button")];
    const btn = buttons.find((b) => b.textContent?.trim() === label);
    expect(btn, `expected button "${label}"`).toBeDefined();
    flushSync(() => {
      btn!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await settle();
  }

  async function pressKey(key: string): Promise<void> {
    flushSync(() => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", {
          key,
          code: key,
          bubbles: true,
          cancelable: true,
        }),
      );
    });
    await settle();
  }

  it("renders step 1 by default", async () => {
    await renderWalkthrough();
    expect(stepTitle()).toBe(STEPS[0].title);
    expect(container.textContent).toContain(PAGE.stepOf(1, STEPS.length));
    expect(container.textContent).toContain(PAGE.exampleTag);
  });

  it("opens step 4 when ?step=4 is in the URL", async () => {
    setStepQuery(4);
    await renderWalkthrough();
    await waitFor(() => stepTitle() === STEPS[3].title, "step 4 title from ?step=4");
    expect(container.textContent).toContain(PAGE.stepOf(4, STEPS.length));
  });

  it("Next and Back change steps", async () => {
    await renderWalkthrough();
    await clickButton(PAGE.next);
    await waitFor(() => stepTitle() === STEPS[1].title, "after Next");
    await clickButton(PAGE.back);
    await waitFor(() => stepTitle() === STEPS[0].title, "after Back");
  });

  it("arrow keys change steps", async () => {
    await renderWalkthrough();
    await pressKey("ArrowRight");
    await waitFor(() => stepTitle() === STEPS[1].title, "after ArrowRight");
    await pressKey("ArrowLeft");
    await waitFor(() => stepTitle() === STEPS[0].title, "after ArrowLeft");
  });

  it("last step button reads Start over and returns to step 1", async () => {
    setStepQuery(STEPS.length);
    await renderWalkthrough();
    await waitFor(() => stepTitle() === STEPS[STEPS.length - 1].title, "last step from ?step=");
    const restart = [...container.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === PAGE.restart,
    );
    expect(restart, "expected Start over").toBeDefined();
    flushSync(() => {
      restart!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await settle();
    await waitFor(() => stepTitle() === STEPS[0].title, "after Start over");
  });

  it("shows Illustrative example on every step", async () => {
    await renderWalkthrough();
    for (let i = 0; i < STEPS.length; i++) {
      if (i > 0) {
        await clickButton(PAGE.next);
      }
      await waitFor(() => stepTitle() === STEPS[i].title, `step ${i + 1} title`);
      expect(container.textContent).toContain(PAGE.exampleTag);
    }
  });

  it("step 4 with both models off shows Forecast unavailable", async () => {
    setStepQuery(4);
    await renderWalkthrough();
    await waitFor(() => stepTitle() === STEPS[3].title, "step 4 for ensemble");
    const modelButtons = [...container.querySelectorAll("button[aria-pressed]")];
    expect(modelButtons.length).toBeGreaterThanOrEqual(2);
    for (const btn of modelButtons) {
      if (btn.getAttribute("aria-pressed") === "true") {
        flushSync(() => {
          btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
        await settle();
      }
    }
    await waitFor(
      () => (container.textContent ?? "").includes("Forecast unavailable"),
      "Forecast unavailable",
    );
  });
});
