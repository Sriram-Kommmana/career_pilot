import { Badge } from "@/components/ui/badge";
import { formatScore } from "@/lib/format";
import { CheckCircle2, XCircle, HelpCircle, Info } from "lucide-react";
import type { MatchAssessmentOut } from "@/types/api";

/**
 * Renders a stored match assessment: the score with its explicit "ranking
 * heuristic, not a probability" label, the component breakdown, and the
 * why/evidence/missing/uncertain/concerns explanation.
 *
 * Never renders a bare percentage without `score_meaning` beside it — that
 * label is what stops the number being misread as a chance of an offer.
 */
export function MatchBreakdown({ match }: { match: MatchAssessmentOut }) {
  if (!match.passed_hard_filters) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
        <p className="mb-2 text-sm font-medium text-slate-900">
          Filtered out by your own constraints
        </p>
        <ul className="space-y-1 text-sm text-slate-600">
          {match.hard_filter_failures.map((failure) => (
            <li key={failure.code} className="flex items-start gap-2">
              <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-slate-400" />
              <span>{failure.message}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <div className="flex h-16 w-16 flex-shrink-0 items-center justify-center rounded-full bg-brand-50 text-lg font-bold text-brand-700">
          {formatScore(match.score)}
        </div>
        <div>
          <p className="text-sm font-medium text-slate-900">Match score</p>
          <p className="flex items-start gap-1 text-xs text-slate-500">
            <Info className="mt-0.5 h-3 w-3 flex-shrink-0" />
            {match.score_meaning}
          </p>
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Score breakdown
        </p>
        {match.components.map((component) => (
          <div key={component.name} className="flex items-center gap-3 text-sm">
            <span className="w-32 flex-shrink-0 capitalize text-slate-600">
              {component.name.replace(/_/g, " ")}
            </span>
            {component.assessed ? (
              <>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-brand-500"
                    style={{ width: `${(component.raw ?? 0) * 100}%` }}
                  />
                </div>
                <span className="w-10 flex-shrink-0 text-right text-xs text-slate-500">
                  {Math.round((component.raw ?? 0) * 100)}%
                </span>
              </>
            ) : (
              <span className="text-xs italic text-slate-400">not assessed</span>
            )}
          </div>
        ))}
      </div>

      {match.explanation && (
        <div className="space-y-3">
          {match.explanation.why_it_may_fit.length > 0 && (
            <ExplanationSection
              icon={CheckCircle2}
              iconClass="text-emerald-500"
              title="Why this may fit"
              items={match.explanation.why_it_may_fit}
            />
          )}
          {match.explanation.evidenced_qualifications.length > 0 && (
            <ExplanationSection
              icon={CheckCircle2}
              iconClass="text-emerald-500"
              title="Evidenced qualifications"
              items={match.explanation.evidenced_qualifications}
            />
          )}
          {match.explanation.missing_requirements.length > 0 && (
            <ExplanationSection
              icon={XCircle}
              iconClass="text-red-500"
              title="Missing requirements"
              items={match.explanation.missing_requirements}
            />
          )}
          {match.explanation.uncertain_requirements.length > 0 && (
            <ExplanationSection
              icon={HelpCircle}
              iconClass="text-amber-500"
              title="Could not assess"
              items={match.explanation.uncertain_requirements}
            />
          )}
          {match.explanation.eligibility_concerns.length > 0 && (
            <ExplanationSection
              icon={HelpCircle}
              iconClass="text-amber-500"
              title="Eligibility concerns"
              items={match.explanation.eligibility_concerns}
            />
          )}
          <div className="rounded-lg bg-brand-50 p-3 text-sm text-brand-900">
            <span className="font-medium">Suggested next step: </span>
            {match.explanation.suggested_next_step}
          </div>
        </div>
      )}

      {match.requirement_results.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Requirements
          </p>
          <ul className="space-y-1.5">
            {match.requirement_results.map((req, idx) => (
              <li key={idx} className="flex items-start gap-2 text-sm">
                <RequirementIcon status={req.status} />
                <div>
                  <span className="text-slate-700">{req.text}</span>
                  {req.is_optional && (
                    <Badge variant="outline" className="ml-2">
                      preferred
                    </Badge>
                  )}
                  {req.note && <p className="text-xs text-slate-500">{req.note}</p>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ExplanationSection({
  icon: Icon,
  iconClass,
  title,
  items,
}: {
  icon: typeof CheckCircle2;
  iconClass: string;
  title: string;
  items: string[];
}) {
  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {title}
      </p>
      <ul className="space-y-1">
        {items.map((item, idx) => (
          <li key={idx} className="flex items-start gap-2 text-sm text-slate-700">
            <Icon className={`mt-0.5 h-4 w-4 flex-shrink-0 ${iconClass}`} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RequirementIcon({ status }: { status: "MET" | "MISSING" | "UNCERTAIN" }) {
  if (status === "MET") {
    return <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" />;
  }
  if (status === "MISSING") {
    return <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" />;
  }
  return <HelpCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />;
}
