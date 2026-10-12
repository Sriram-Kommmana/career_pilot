import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ApplicationPackagePanel } from "@/components/ApplicationPackagePanel";
import type { ApplicationPackageOut } from "@/types/api";

function buildPackage(overrides: Partial<ApplicationPackageOut> = {}): ApplicationPackageOut {
  return {
    fit_summary: {
      score: 0.8,
      score_kind: "RANKING_HEURISTIC",
      score_meaning:
        "A ranking heuristic used to order jobs. It is not a probability of an interview or an offer.",
      passed_hard_filters: true,
      hard_filter_failures: [],
      components: [],
      requirement_results: [],
      explanation: {
        why_it_may_fit: [],
        evidenced_qualifications: [],
        missing_requirements: [],
        uncertain_requirements: [],
        eligibility_concerns: [],
        suggested_next_step: "Review and confirm the drafts below.",
      },
      evidence_refs: [],
      assessed_weight: 0.9,
      engine_version: "1.0.0",
      assessed_at: "2026-06-15T12:00:00Z",
    },
    resume_guidance: [
      {
        kind: "experience",
        id: "11111111-1111-1111-1111-111111111111",
        title: "Platform Engineer",
        organisation: "Fixture Systems",
        matched_skills: ["python", "kafka"],
        highlights: [],
        reason: "Matches python, kafka from your confirmed experience.",
        evidence_ref: { kind: "experience", id: "11111111-1111-1111-1111-111111111111" },
      },
    ],
    readiness_checklist: [
      {
        category: "cover_letter",
        label: "Cover letter",
        status: "needs_attention",
        detail: "AI-drafted — review before using.",
      },
    ],
    application: {} as ApplicationPackageOut["application"],
    ...overrides,
  };
}

describe("ApplicationPackagePanel", () => {
  it("shows an empty state and a Generate button before anything exists", () => {
    const onGenerate = vi.fn();
    render(<ApplicationPackagePanel pkg={undefined} isGenerating={false} onGenerate={onGenerate} />);
    expect(screen.getByText("Not generated yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /generate/i }));
    expect(onGenerate).toHaveBeenCalledOnce();
  });

  it("renders the fit summary, resume guidance, and readiness checklist once generated", () => {
    render(
      <ApplicationPackagePanel pkg={buildPackage()} isGenerating={false} onGenerate={vi.fn()} />,
    );
    expect(screen.getByText("Platform Engineer")).toBeInTheDocument();
    expect(screen.getByText(/Matches python, kafka/)).toBeInTheDocument();
    expect(screen.getByText("Cover letter")).toBeInTheDocument();
    expect(screen.getByText("AI-drafted — review before using.")).toBeInTheDocument();
    // The score is still labelled a ranking heuristic via the reused MatchBreakdown.
    expect(screen.getByText(/ranking heuristic/i)).toBeInTheDocument();
  });

  it("shows an honest empty state for resume guidance instead of a guess", () => {
    render(
      <ApplicationPackagePanel
        pkg={buildPackage({ resume_guidance: [] })}
        isGenerating={false}
        onGenerate={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/No confirmed project or experience currently overlaps/),
    ).toBeInTheDocument();
  });

  it("shows Regenerate, not Generate, once a package already exists", () => {
    render(
      <ApplicationPackagePanel pkg={buildPackage()} isGenerating={false} onGenerate={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /regenerate/i })).toBeInTheDocument();
  });
});
