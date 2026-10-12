import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { StatusBadge } from "@/components/StatusBadge";
import { DemoDataBadge } from "@/components/DemoDataBadge";
import { formatDateTime, formatStateLabel } from "@/lib/format";
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  HelpCircle,
  Lock,
  ShieldAlert,
  ExternalLink,
} from "lucide-react";
import type {
  ApplicationDetailOut,
  ApplicationFieldOut,
  ApplicationOutcome,
  ApplicationReviewOut,
} from "@/types/api";

const OUTCOMES: ApplicationOutcome[] = [
  "NONE",
  "ACKNOWLEDGED",
  "INTERVIEW",
  "OFFER",
  "REJECTED",
  "WITHDRAWN",
  "CLOSED",
];

export function ApplicationDetail() {
  const { applicationId } = useParams<{ applicationId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);
  const [fieldDrafts, setFieldDrafts] = useState<Record<string, string>>({});
  const [manualNote, setManualNote] = useState("");
  const [manualConfirmed, setManualConfirmed] = useState(false);

  const detailQuery = useQuery({
    queryKey: ["application", applicationId],
    queryFn: () => api.get<ApplicationDetailOut>(`/applications/${applicationId}`),
    enabled: !!applicationId,
  });

  const reviewQuery = useQuery({
    queryKey: ["application", applicationId, "review"],
    queryFn: () => api.get<ApplicationReviewOut>(`/applications/${applicationId}/review`),
    enabled: !!applicationId && !!detailQuery.data,
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["application", applicationId] });
    queryClient.invalidateQueries({ queryKey: ["tracker"] });
  }

  const prepareMutation = useMutation({
    mutationFn: () => api.post<ApplicationDetailOut>(`/applications/${applicationId}/prepare`),
    onSuccess: invalidate,
    onError: (e) => setActionError(describeError(e)),
  });

  const answerMutation = useMutation({
    mutationFn: (payload: { field_key: string; value: string }) =>
      api.post<ApplicationDetailOut>(`/applications/${applicationId}/answer`, payload),
    onSuccess: (_, variables) => {
      setFieldDrafts((prev) => {
        const next = { ...prev };
        delete next[variables.field_key];
        return next;
      });
      invalidate();
    },
    onError: (e) => setActionError(describeError(e)),
  });

  const approveMutation = useMutation({
    mutationFn: (formStateHash: string) =>
      api.post<ApplicationDetailOut>(`/applications/${applicationId}/approve`, {
        form_state_hash: formStateHash,
      }),
    onSuccess: () => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["application", applicationId, "review"] });
    },
    onError: (e) => setActionError(describeError(e)),
  });

  const submitMutation = useMutation({
    mutationFn: (formStateHash: string) =>
      api.post<ApplicationDetailOut>(`/applications/${applicationId}/submit`, {
        confirm: true,
        form_state_hash: formStateHash,
      }),
    onSuccess: () => {
      invalidate();
      queryClient.invalidateQueries({ queryKey: ["application", applicationId, "review"] });
    },
    onError: (e) => setActionError(describeError(e)),
  });

  const cancelMutation = useMutation({
    mutationFn: () => api.post<ApplicationDetailOut>(`/applications/${applicationId}/cancel`, {}),
    onSuccess: invalidate,
    onError: (e) => setActionError(describeError(e)),
  });

  const outcomeMutation = useMutation({
    mutationFn: (outcome: ApplicationOutcome) =>
      api.patch<ApplicationDetailOut>(`/applications/${applicationId}/outcome`, { outcome }),
    onSuccess: invalidate,
    onError: (e) => setActionError(describeError(e)),
  });

  const manualSubmitMutation = useMutation({
    mutationFn: () =>
      api.post<ApplicationDetailOut>(`/applications/${applicationId}/record-manual-submission`, {
        confirm: true,
        note: manualNote || undefined,
      }),
    onSuccess: () => {
      invalidate();
      navigate(`/applications/${applicationId}`);
    },
    onError: (e) => setActionError(describeError(e)),
  });

  if (detailQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (detailQuery.isError) {
    return <ErrorState error={detailQuery.error} onRetry={() => detailQuery.refetch()} />;
  }
  const application = detailQuery.data;
  if (!application) return null;

  const decision = reviewQuery.data?.decision;
  const canPrepare = ["SELECTED", "READY", "WAITING_FOR_USER_INPUT", "FAILED"].includes(
    application.state,
  );
  const canApprove = application.state === "WAITING_FOR_APPROVAL" && !!application.form_state_hash;
  const canSubmit = decision?.allowed === true && !!application.form_state_hash;
  const isTerminal = ["SUBMITTED", "CANCELLED"].includes(application.state);
  const alreadySubmitted = !!application.submitted_at;

  return (
    <div className="space-y-6">
      <Link
        to="/tracker"
        className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to tracker
      </Link>

      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-slate-900">{application.job.title}</h1>
            {application.job.is_demo_data && <DemoDataBadge />}
          </div>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-slate-600">
            <Building2 className="h-4 w-4" /> {application.job.company.name}
          </p>
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          <StatusBadge state={application.state} />
        </div>
      </div>

      {actionError && <Alert variant="danger">{actionError}</Alert>}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {canPrepare && (
            <Card>
              <CardContent className="flex items-center justify-between p-5">
                <div>
                  <p className="text-sm font-medium text-slate-900">
                    {application.fields.length === 0
                      ? "Draft this application from your confirmed profile"
                      : "Re-run preparation"}
                  </p>
                  <p className="mt-0.5 text-sm text-slate-500">
                    Nothing is filled in on the employer's site. This only drafts
                    answers from facts you've confirmed.
                  </p>
                </div>
                <Button
                  onClick={() => prepareMutation.mutate()}
                  isLoading={prepareMutation.isPending}
                >
                  {application.fields.length === 0 ? "Prepare" : "Re-prepare"}
                </Button>
              </CardContent>
            </Card>
          )}

          {application.fields.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Application fields</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {application.fields.map((field) => (
                  <FieldRow
                    key={field.id}
                    field={field}
                    draft={fieldDrafts[field.field_key] ?? ""}
                    onDraftChange={(value) =>
                      setFieldDrafts((prev) => ({ ...prev, [field.field_key]: value }))
                    }
                    onSubmit={() =>
                      answerMutation.mutate({
                        field_key: field.field_key,
                        value: fieldDrafts[field.field_key] ?? "",
                      })
                    }
                    isSaving={
                      answerMutation.isPending &&
                      answerMutation.variables?.field_key === field.field_key
                    }
                  />
                ))}
              </CardContent>
            </Card>
          )}

          {application.events.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Activity</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-3">
                  {application.events
                    .slice()
                    .reverse()
                    .map((event) => (
                      <li key={event.id} className="flex items-start gap-3 text-sm">
                        <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-slate-300" />
                        <div>
                          <p className="text-slate-700">{event.message ?? event.event_type}</p>
                          <p className="text-xs text-slate-400">
                            {formatDateTime(event.occurred_at)}
                            {event.to_state && ` · ${formatStateLabel(event.to_state)}`}
                          </p>
                        </div>
                      </li>
                    ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Submission gate</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {reviewQuery.isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : decision ? (
                decision.allowed ? (
                  <Alert variant="success">
                    Every condition is satisfied. You may approve and submit.
                  </Alert>
                ) : (
                  <div className="space-y-2">
                    <p className="text-sm text-slate-600">
                      This application cannot be submitted yet:
                    </p>
                    <ul className="space-y-1.5">
                      {decision.blockers.map((blocker) => (
                        <li key={blocker.code} className="flex items-start gap-2 text-sm">
                          <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
                          <span className="text-slate-700">{blocker.message}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )
              ) : null}

              <div className="flex flex-col gap-2 pt-2">
                <Button
                  variant="outline"
                  disabled={!canApprove}
                  isLoading={approveMutation.isPending}
                  onClick={() =>
                    application.form_state_hash &&
                    approveMutation.mutate(application.form_state_hash)
                  }
                >
                  <Lock className="h-4 w-4" /> Approve current form
                </Button>
                <Button
                  disabled={!canSubmit}
                  isLoading={submitMutation.isPending}
                  onClick={() =>
                    application.form_state_hash && submitMutation.mutate(application.form_state_hash)
                  }
                >
                  Submit application
                </Button>
              </div>
            </CardContent>
            <CardFooter className="flex-col items-start gap-1 text-xs text-slate-400">
              <p>
                An explicit backend gate enforces this — not this screen. See{" "}
                <a href={application.job.application_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline">
                  the original posting <ExternalLink className="h-3 w-3" />
                </a>
                .
              </p>
            </CardFooter>
          </Card>

          {!isTerminal && !alreadySubmitted && (
            <Card>
              <CardHeader>
                <CardTitle>Applied yourself?</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-slate-500">
                  The browser agent that fills and verifies forms on the employer's
                  site is not built yet. If you applied directly, record it here —
                  this is explicitly marked as not verified by CareerPilot.
                </p>
                <Textarea
                  placeholder="Optional note"
                  value={manualNote}
                  onChange={(e) => setManualNote(e.target.value)}
                />
                <label className="flex items-start gap-2 text-sm text-slate-600">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={manualConfirmed}
                    onChange={(e) => setManualConfirmed(e.target.checked)}
                  />
                  I confirm I submitted this application myself, outside CareerPilot.
                </label>
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={!manualConfirmed}
                  isLoading={manualSubmitMutation.isPending}
                  onClick={() => manualSubmitMutation.mutate()}
                >
                  Record manual submission
                </Button>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Outcome</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Select
                value={application.outcome}
                onChange={(e) => outcomeMutation.mutate(e.target.value as ApplicationOutcome)}
              >
                {OUTCOMES.map((outcome) => (
                  <option key={outcome} value={outcome}>
                    {formatStateLabel(outcome)}
                  </option>
                ))}
              </Select>
              {!isTerminal && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full text-red-600 hover:bg-red-50"
                  isLoading={cancelMutation.isPending}
                  onClick={() => cancelMutation.mutate()}
                >
                  Cancel this application
                </Button>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function FieldRow({
  field,
  draft,
  onDraftChange,
  onSubmit,
  isSaving,
}: {
  field: ApplicationFieldOut;
  draft: string;
  onDraftChange: (value: string) => void;
  onSubmit: () => void;
  isSaving: boolean;
}) {
  const needsAnswer = field.resolution === "NEEDS_HUMAN" || field.resolution === "UNKNOWN_FIELD";

  return (
    <div className="rounded-lg border border-slate-200 p-3.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-slate-900">
            {field.label ?? field.field_key}
          </p>
          {field.is_required && (
            <Badge variant="outline" className="text-[10px]">
              required
            </Badge>
          )}
        </div>
        <ResolutionBadge resolution={field.resolution} />
      </div>

      {field.resolution === "RESOLVED" ? (
        <p className="mt-1.5 text-sm text-slate-600">
          {field.value_masked}
          <span className="ml-2 text-xs text-slate-400">
            source: {field.value_provenance.toLowerCase().replace(/_/g, " ")}
          </span>
        </p>
      ) : field.resolution === "SKIPPED" ? (
        <p className="mt-1.5 text-xs italic text-slate-400">Left blank (optional)</p>
      ) : needsAnswer ? (
        <div className="mt-2 flex items-start gap-2">
          {field.field_type === "textarea" ? (
            <Textarea
              value={draft}
              onChange={(e) => onDraftChange(e.target.value)}
              className="flex-1"
              rows={3}
            />
          ) : (
            <Input
              value={draft}
              onChange={(e) => onDraftChange(e.target.value)}
              className="flex-1"
            />
          )}
          <Button size="sm" onClick={onSubmit} isLoading={isSaving} disabled={!draft.trim()}>
            Save
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function ResolutionBadge({ resolution }: { resolution: ApplicationFieldOut["resolution"] }) {
  if (resolution === "RESOLVED") {
    return (
      <Badge variant="success">
        <CheckCircle2 className="h-3 w-3" /> Resolved
      </Badge>
    );
  }
  if (resolution === "SKIPPED") {
    return <Badge variant="outline">Skipped</Badge>;
  }
  return (
    <Badge variant="warning">
      <HelpCircle className="h-3 w-3" /> Needs you
    </Badge>
  );
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "SUBMISSION_BLOCKED" || error.code === "VALIDATION_ERROR") {
      return error.message;
    }
    return error.message;
  }
  return "Something went wrong.";
}
