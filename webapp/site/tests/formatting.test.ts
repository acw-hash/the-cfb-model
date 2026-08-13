import { describe, expect, it } from "vitest";

import {
  ABSENT,
  FORECAST_UNAVAILABLE,
  NOT_COMPUTED,
  formatIntervalInline,
  formatIntervalParts,
  formatMargin,
  formatProbability,
  formatSigma,
  formatTotal,
  renderForecastUnavailable,
  renderNotComputed,
} from "@/lib/formatting/numbers";

describe("formatMargin", () => {
  it("shows sign and one decimal by default", () => {
    expect(formatMargin(4.146)).toBe("+4.1");
    expect(formatMargin(-1.04)).toBe("\u22121.0");
  });

  it("caps precision to sigma decimals", () => {
    expect(formatMargin(4.146, 16.73)).toBe("+4.1");
    expect(formatMargin(4.146, 0.04)).toBe("+4");
  });
});

describe("formatSigma", () => {
  it("prefixes sigma with one decimal", () => {
    expect(formatSigma(16.732)).toBe("\u03c3 16.7");
  });

  it("returns null for absent sigma", () => {
    expect(formatSigma(null)).toBeNull();
  });
});

describe("formatTotal", () => {
  it("uses one decimal without sign", () => {
    expect(formatTotal(49.734)).toBe("49.7");
  });
});

describe("formatProbability", () => {
  it("uses integer percent when >= 10%", () => {
    expect(formatProbability(0.675879)).toBe("68%");
  });

  it("uses one decimal when < 10%", () => {
    expect(formatProbability(0.094)).toBe("9.4%");
  });
});

describe("formatIntervalParts", () => {
  it("renders mu [lo, hi] quiet band", () => {
    const parts = formatIntervalParts(4.2, -8.1, 16.5, 13.8);
    expect(parts).not.toBeNull();
    expect(formatIntervalInline(parts!)).toBe("+4.2 [\u22128.1, +16.5]");
  });

  it("omits band when bounds null", () => {
    const parts = formatIntervalParts(4.2, null, null, 13.8);
    expect(formatIntervalInline(parts!)).toBe("+4.2");
  });
});

describe("honest absence (§1.8)", () => {
  it("renders forecast unavailable with null_reason tooltip", () => {
    const result = renderForecastUnavailable("cold_start_insufficient");
    expect(result.text).toBe(FORECAST_UNAVAILABLE);
    expect(result.title).toBe("cold_start_insufficient");
  });

  it("renders not computed for probabilities", () => {
    expect(renderNotComputed()).toBe(NOT_COMPUTED);
  });

  it("uses em dash for generic absence", () => {
    expect(ABSENT).toBe("\u2014");
  });
});

describe("fixture-derived null cases (doctored ADR 0014)", () => {
  it("cold_start_insufficient maps to forecast unavailable", () => {
    expect(renderForecastUnavailable("cold_start_insufficient").text).toBe(FORECAST_UNAVAILABLE);
  });
});
