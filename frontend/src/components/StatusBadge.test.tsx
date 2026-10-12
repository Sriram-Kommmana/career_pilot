import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "@/components/StatusBadge";
import type { ApplicationState } from "@/types/api";

describe("StatusBadge", () => {
  it("renders a human-readable label for every application state", () => {
    const states: ApplicationState[] = [
      "DISCOVERED",
      "MATCHED",
      "SELECTED",
      "PREPARING",
      "READY",
      "FILLING",
      "VERIFYING",
      "WAITING_FOR_USER_INPUT",
      "WAITING_FOR_APPROVAL",
      "SUBMITTING",
      "SUBMITTED",
      "SUBMITTED_UNCERTAIN",
      "FAILED",
      "CANCELLED",
    ];
    for (const state of states) {
      const { unmount } = render(<StatusBadge state={state} />);
      // Every state must render *something* readable — never the raw
      // SCREAMING_SNAKE_CASE value, which is an implementation detail.
      expect(screen.getByText(/^[A-Z][a-z]/)).toBeInTheDocument();
      unmount();
    }
  });

  it("renders waiting states distinctly from submitted", () => {
    render(<StatusBadge state="WAITING_FOR_APPROVAL" />);
    expect(screen.getByText("Waiting For Approval")).toBeInTheDocument();
  });
});
