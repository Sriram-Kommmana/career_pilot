import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/EmptyState";
import { MatchBreakdown } from "@/components/MatchBreakdown";
import { CheckCircle2, HelpCircle, Sparkles, XCircle } from "lucide-react";
import type { ApplicationPackageOut, ReadinessStatus } from "@/types/api";

/**
 * The job-specific preparation package: the fit summary (the existing match
 * assessment, rendered by the same MatchBreakdown every job detail page
 * already uses), resume-emphasis guidance, and the readiness checklist.
 *
 * The cover letter draft and question-answer drafts live on
 * `application.fields`/`application.answers` and are rendered by
 * ApplicationDetail.tsx's own FieldRow/AnswerRow — this panel only shows what
 * doesn't already have a place on that page.
 */
export function ApplicationPackagePanel({
  pkg,
  isGenerating,
  onGenerate,
}: {
  pkg: ApplicationPackageOut | undefined;
  isGenerating: boolean;
  onGenerate: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle>Application package</CardTitle>
            <CardDescription>
              A fit summary, resume guidance, and a readiness checklist for this
              job — grounded only in what you've confirmed.
            </CardDescription>
          </div>
          <Button size="sm" onClick={onGenerate} isLoading={isGenerating}>
            <Sparkles className="h-4 w-4" />
            {pkg ? "Regenerate" : "Generate"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {isGenerating && !pkg ? (
          <Skeleton className="h-48 w-full" />
        ) : !pkg ? (
          <EmptyState
            icon={Sparkles}
            title="Not generated yet"
            description="Generate a fit summary, resume guidance, a cover-letter draft, and question drafts from your confirmed profile."
          />
        ) : (
          <>
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Fit summary
              </p>
              <MatchBreakdown match={pkg.fit_summary} />
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Resume guidance
              </p>
              {pkg.resume_guidance.length === 0 ? (
                <p className="text-sm italic text-slate-400">
                  No confirmed project or experience currently overlaps this
                  job's requirements. Add confirmed skills, projects, or
                  experience to your profile, then regenerate.
                </p>
              ) : (
                <ul className="space-y-2">
                  {pkg.resume_guidance.map((item) => (
                    <li
                      key={`${item.kind}-${item.id}`}
                      className="rounded-lg border border-slate-200 p-3"
                    >
                      <p className="text-sm font-medium text-slate-900">{item.title}</p>
                      <p className="text-xs text-slate-500">{item.reason}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                Readiness checklist
              </p>
              <ul className="space-y-1.5">
                {pkg.readiness_checklist.map((item, idx) => (
                  <li key={idx} className="flex items-start gap-2 text-sm">
                    <ReadinessIcon status={item.status} />
                    <div>
                      <span className="text-slate-700">{item.label}</span>
                      <p className="text-xs text-slate-500">{item.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function ReadinessIcon({ status }: { status: ReadinessStatus }) {
  if (status === "ready") {
    return <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" />;
  }
  if (status === "missing") {
    return <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" />;
  }
  return <HelpCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />;
}

/** Shared "this is a draft, review it" badge — used by FieldRow and AnswerRow. */
export function DraftBadge() {
  return (
    <Badge variant="brand">
      <Sparkles className="h-3 w-3" /> AI-drafted
    </Badge>
  );
}
