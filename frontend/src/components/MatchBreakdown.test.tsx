import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MatchBreakdown } from "@/components/MatchBreakdown";
import type { MatchAssessmentOut } from "@/types/api";

function buildMatch(overrides: Partial<MatchAssessmentOut> = {}): MatchAssessmentOut {
  return {
    score: 0.72,
    score_kind: "RANKING_HEURISTIC",
    score_meaning:
      "A ranking heuristic used to order jobs. It is not a probability of an interview or an offer.",
    passed_hard_filters: true,
    hard_filter_failures: [],
    components: [
      {
        name: "skill_overlap",
        raw: 0.8,
        weight: 0.3,
        weighted: 0.24,
        assessed: true,
        rationale: "4 of 5 required items matched.",
        details: {},
      },
      {
        name: "education",
        raw: null,
        weight: 0.1,
        weighted: 0,
        assessed: false,
        rationale: "The posting does not state an education requirement.",
        details: {},
      },
    ],
    requirement_results: [],
    explanation: {
      why_it_may_fit: ["Strong skill overlap."],
      evidenced_qualifications: ["Python — evidenced by a project."],
      missing_requirements: [],
      uncertain_requirements: [],
      eligibility_concerns: [],
      suggested_next_step: "Prepare the application and review the drafted answers.",
    },
    evidence_refs: [],
    assessed_weight: 0.9,
    engine_version: "1.0.0",
    assessed_at: "2026-06-15T12:00:00Z",
    ...overrides,
  };
}

describe("MatchBreakdown", () => {
  it("always shows the score is a ranking heuristic, not a probability", () => {
    render(<MatchBreakdown match={buildMatch()} />);
    expect(screen.getByText(/ranking heuristic/i)).toBeInTheDocument();
    expect(screen.getByText(/not a probability/i)).toBeInTheDocument();
  });

  it("renders the score as a rounded percentage", () => {
    render(<MatchBreakdown match={buildMatch({ score: 0.72 })} />);
    expect(screen.getByText("72%")).toBeInTheDocument();
  });

  it("marks an unassessed component as 'not assessed' rather than showing 0%", () => {
    render(<MatchBreakdown match={buildMatch()} />);
    // The education component (assessed: false) must read as "not assessed" —
    // never as a zero score, which would misrepresent the job's own gap as the
    // candidate's failing.
    expect(screen.getByText("not assessed")).toBeInTheDocument();
  });

  it("shows why-it-may-fit and evidenced qualifications when present", () => {
    render(<MatchBreakdown match={buildMatch()} />);
    expect(screen.getByText("Strong skill overlap.")).toBeInTheDocument();
    expect(screen.getByText(/Python — evidenced by a project/)).toBeInTheDocument();
  });

  it("shows the suggested next step, and it never says 'apply now'", () => {
    render(<MatchBreakdown match={buildMatch()} />);
    expect(screen.getByText(/Prepare the application/)).toBeInTheDocument();
    expect(screen.queryByText(/apply now/i)).not.toBeInTheDocument();
  });

  it("renders hard-filter failures instead of a score when the job was excluded", () => {
    const match = buildMatch({
      passed_hard_filters: false,
      score: null,
      hard_filter_failures: [
        { code: "JOB_CLOSED", message: "The source reports this posting as closed.", detail: null },
      ],
    });
    render(<MatchBreakdown match={match} />);
    expect(screen.getByText("Filtered out by your own constraints")).toBeInTheDocument();
    expect(
      screen.getByText("The source reports this posting as closed."),
    ).toBeInTheDocument();
    // No score widget should render for an excluded job.
    expect(screen.queryByText("Match score")).not.toBeInTheDocument();
  });
});
