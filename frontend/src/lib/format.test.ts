import { describe, expect, it } from "vitest";
import {
  formatPostedDate,
  formatSalary,
  formatScore,
  formatStateLabel,
  formatWorkArrangement,
} from "@/lib/format";

describe("formatPostedDate", () => {
  it("never renders an unknown posting date as recent", () => {
    // This is the one case that matters most: a missing date must read as
    // "not provided", never be mistaken for a fresh posting.
    expect(formatPostedDate(null, "UNKNOWN")).toBe("Posting date not provided");
  });

  it("renders a date-only precision without claiming exactness", () => {
    const result = formatPostedDate("2026-06-01T00:00:00Z", "DATE_ONLY");
    expect(result).not.toContain("(exact)");
    expect(result).toContain("2026");
  });

  it("marks an exact timestamp as exact", () => {
    const result = formatPostedDate("2026-06-01T09:00:00Z", "EXACT");
    expect(result).toContain("(exact)");
  });
});

describe("formatScore", () => {
  it("renders a null score as not ranked, never as zero", () => {
    // A job excluded by a hard filter, or one that could not be assessed, carries
    // score=null. Rendering it as "0%" would misrepresent it as a bad match.
    expect(formatScore(null)).toBe("Not ranked");
  });

  it("renders a score as a rounded percentage", () => {
    expect(formatScore(0.734)).toBe("73%");
  });

  it("renders zero itself correctly, distinct from null", () => {
    expect(formatScore(0)).toBe("0%");
  });
});

describe("formatSalary", () => {
  it("returns null when nothing is stated, rather than a placeholder string", () => {
    expect(formatSalary(null, null, null, null)).toBeNull();
  });

  it("renders a range with both currency and period", () => {
    const result = formatSalary(80000, 120000, "USD", "year");
    expect(result).toContain("USD");
    expect(result).toContain("year");
    expect(result).toContain("80,000");
    expect(result).toContain("120,000");
  });

  it("renders a one-sided range without inventing the other side", () => {
    const result = formatSalary(null, 100000, "INR", "year");
    expect(result).not.toContain("–");
    expect(result).toContain("100,000");
  });
});

describe("formatWorkArrangement / formatStateLabel", () => {
  it("gives every work arrangement a human label", () => {
    expect(formatWorkArrangement("REMOTE")).toBe("Remote");
    expect(formatWorkArrangement("UNKNOWN")).not.toBe("UNKNOWN");
  });

  it("title-cases a SCREAMING_SNAKE_CASE state", () => {
    expect(formatStateLabel("WAITING_FOR_APPROVAL")).toBe("Waiting For Approval");
  });
});
