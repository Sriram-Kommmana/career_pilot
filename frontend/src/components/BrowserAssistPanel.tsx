import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Globe,
  Search,
  CheckCircle2,
  XCircle,
  HelpCircle,
  ShieldAlert,
  X,
} from "lucide-react";
import type {
  ApplicationState,
  BrowserSessionOut,
  FillValidationReportOut,
  InspectionResultOut,
  ProposedMappingOut,
} from "@/types/api";

interface MessageResponse {
  message: string;
}

/**
 * Phase 5's browser-assisted fill, for one application: open an isolated
 * session, inspect the real employer page, review the proposed mapping
 * (Approval A), fill only the approved fields, and show the real
 * verification result. This never submits anything — the existing
 * "Submission gate" card on this page (Approval B, unchanged) is the only
 * place that can do that, and only once this panel's result says every
 * required field was actually verified on the live page.
 */
export function BrowserAssistPanel({
  applicationId,
  applicationState,
  onFilled,
}: {
  applicationId: string;
  applicationState: ApplicationState;
  onFilled: () => void;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [inspection, setInspection] = useState<InspectionResultOut | undefined>(undefined);
  const [report, setReport] = useState<FillValidationReportOut | undefined>(undefined);
  const [excludedFieldIds, setExcludedFieldIds] = useState<Set<string>>(new Set());

  const sessionKey = ["application", applicationId, "browser-session"];

  const sessionQuery = useQuery({
    queryKey: sessionKey,
    queryFn: async (): Promise<BrowserSessionOut | null> => {
      try {
        return await api.get<BrowserSessionOut>(`/applications/${applicationId}/browser-session`);
      } catch (e) {
        if (e instanceof ApiError && e.code === "BROWSER_SESSION_NOT_FOUND") return null;
        throw e;
      }
    },
  });

  function resetLocalState() {
    setInspection(undefined);
    setReport(undefined);
    setExcludedFieldIds(new Set());
  }

  const startMutation = useMutation({
    mutationFn: () => api.post<BrowserSessionOut>(`/applications/${applicationId}/browser-session`),
    onSuccess: () => {
      resetLocalState();
      setError(null);
      queryClient.invalidateQueries({ queryKey: sessionKey });
    },
    onError: (e) => setError(describeError(e)),
  });

  const inspectMutation = useMutation({
    mutationFn: () =>
      api.post<InspectionResultOut>(`/applications/${applicationId}/browser-session/inspect`),
    onSuccess: (result) => {
      setInspection(result);
      setReport(undefined);
      setExcludedFieldIds(new Set());
      setError(null);
    },
    onError: (e) => setError(describeError(e)),
  });

  const fillMutation = useMutation({
    mutationFn: () => {
      if (!inspection?.mapping_token) {
        throw new Error("Inspect the form again before filling it.");
      }
      return api.post<FillValidationReportOut>(
        `/applications/${applicationId}/browser-session/fill`,
        {
          mapping_token: inspection.mapping_token,
          excluded_field_ids: Array.from(excludedFieldIds),
        },
      );
    },
    onSuccess: (result) => {
      setReport(result);
      setError(null);
      onFilled();
    },
    onError: (e) => setError(describeError(e)),
  });

  const closeMutation = useMutation({
    mutationFn: () => api.delete<MessageResponse>(`/applications/${applicationId}/browser-session`),
    onSuccess: () => {
      resetLocalState();
      setError(null);
      queryClient.invalidateQueries({ queryKey: sessionKey });
    },
    onError: (e) => setError(describeError(e)),
  });

  const session = sessionQuery.data ?? null;
  const canStart = applicationState === "READY" && !session;
  const canFill = applicationState === "READY";
  const fillableFields = (inspection?.mappings ?? []).filter(
    (m) => m.supported_for_fill && m.proposed_value !== null,
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Browser-assisted fill</CardTitle>
        <CardDescription>
          Opens a real, visible browser window on the employer's own page. Fills
          only the fields you approve, from data you've already confirmed —
          never submits anything.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <Alert variant="danger">{error}</Alert>}

        {sessionQuery.isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : !session ? (
          <div className="space-y-3">
            {!canStart && (
              <p className="text-sm text-slate-500">
                {applicationState === "READY"
                  ? ""
                  : "This application must be in the READY state before a browser session can open — prepare it and resolve every outstanding field first."}
              </p>
            )}
            <Button
              onClick={() => startMutation.mutate()}
              isLoading={startMutation.isPending}
              disabled={!canStart}
            >
              <Globe className="h-4 w-4" /> Open browser session
            </Button>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 p-3">
              <div className="flex items-center gap-2 text-sm">
                <Globe className="h-4 w-4 text-slate-500" />
                <span className="text-slate-700">Session open at</span>
                <Badge variant="outline">{session.origin}</Badge>
              </div>
              <Button
                variant="ghost"
                size="sm"
                isLoading={closeMutation.isPending}
                onClick={() => closeMutation.mutate()}
              >
                <X className="h-3.5 w-3.5" /> Close
              </Button>
            </div>

            {!inspection && (
              <Button
                variant="outline"
                onClick={() => inspectMutation.mutate()}
                isLoading={inspectMutation.isPending}
              >
                <Search className="h-4 w-4" /> Inspect this page
              </Button>
            )}

            {inspection && !inspection.supported && (
              <Alert variant="warning" title="This form isn't supported yet">
                {inspection.reason ?? "This page is not one CareerPilot knows how to fill."}
              </Alert>
            )}

            {inspection && inspection.supported && !report && (
              <div className="space-y-3">
                <Alert variant="info" title="Review before filling">
                  Approving this fills only the fields below marked as ready. It
                  does not submit the form — you'll review the filled page and
                  the submission gate separately decides when it can be sent.
                </Alert>
                <ul className="space-y-2">
                  {inspection.mappings.map((mapping) => (
                    <MappingRow
                      key={mapping.field_id}
                      mapping={mapping}
                      excluded={excludedFieldIds.has(mapping.field_id)}
                      onToggleExclude={() =>
                        setExcludedFieldIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(mapping.field_id)) next.delete(mapping.field_id);
                          else next.add(mapping.field_id);
                          return next;
                        })
                      }
                    />
                  ))}
                </ul>
                <div className="flex items-center gap-2">
                  <Button
                    onClick={() => fillMutation.mutate()}
                    isLoading={fillMutation.isPending}
                    disabled={!canFill || fillableFields.length === 0}
                  >
                    Fill {fillableFields.length} approved field
                    {fillableFields.length === 1 ? "" : "s"}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => inspectMutation.mutate()}
                    isLoading={inspectMutation.isPending}
                  >
                    Re-inspect
                  </Button>
                </div>
                {!canFill && (
                  <p className="text-xs text-slate-500">
                    This application is in {applicationState}, not READY, so
                    filling is disabled until that's resolved.
                  </p>
                )}
              </div>
            )}

            {report && <FillReport report={report} />}
          </div>
        )}
      </CardContent>
      <CardFooter className="text-xs text-slate-400">
        Employer sites may impose their own automation restrictions. You are
        responsible for reviewing anything filled here before it is submitted.
      </CardFooter>
    </Card>
  );
}

function MappingRow({
  mapping,
  excluded,
  onToggleExclude,
}: {
  mapping: ProposedMappingOut;
  excluded: boolean;
  onToggleExclude: () => void;
}) {
  const fillable = mapping.supported_for_fill && mapping.proposed_value !== null;

  return (
    <li className="rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-slate-900">
            {mapping.label ?? mapping.field_id}
          </p>
          {mapping.required && (
            <Badge variant="outline" className="text-[10px]">
              required
            </Badge>
          )}
        </div>
        {fillable ? (
          <label className="flex items-center gap-1.5 text-xs text-slate-500">
            <input
              type="checkbox"
              checked={excluded}
              onChange={onToggleExclude}
              className="h-3.5 w-3.5"
            />
            Exclude
          </label>
        ) : mapping.needs_review ? (
          <Badge variant="warning">
            <HelpCircle className="h-3 w-3" /> Needs you
          </Badge>
        ) : null}
      </div>
      {fillable ? (
        <p className="mt-1.5 text-sm text-slate-600">
          {mapping.proposed_value}
          <span className="ml-2 text-xs text-slate-400">{mapping.reason}</span>
        </p>
      ) : (
        <p className="mt-1.5 text-xs italic text-slate-400">{mapping.reason}</p>
      )}
    </li>
  );
}

function FillReport({ report }: { report: FillValidationReportOut }) {
  return (
    <div className="space-y-3">
      <Alert
        variant={report.failed_count > 0 || report.still_needs_human.length > 0 ? "warning" : "success"}
        title="Nothing was submitted"
      >
        Filled {report.filled_count} field{report.filled_count === 1 ? "" : "s"}, verified
        against the live page. Review the open browser window, then use the
        submission gate below once you're ready to send it.
      </Alert>
      <ul className="space-y-1.5">
        {report.outcomes.map((outcome) => (
          <li key={outcome.field_id} className="flex items-start gap-2 text-sm">
            {outcome.verification === "PASSED" ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" />
            ) : outcome.verification === "FAILED" ? (
              <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" />
            ) : (
              <ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0 text-amber-500" />
            )}
            <div>
              <span className="text-slate-700">{outcome.field_id}</span>
              <span className="ml-2 text-xs text-slate-400">
                {outcome.action_result} · {outcome.verification.toLowerCase()}
              </span>
              {outcome.verification_note && (
                <p className="text-xs text-slate-400">{outcome.verification_note}</p>
              )}
            </div>
          </li>
        ))}
      </ul>
      {report.still_needs_human.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Still needs you
          </p>
          <ul className="mt-1 list-inside list-disc text-sm text-slate-600">
            {report.still_needs_human.map((label) => (
              <li key={label}>{label}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
