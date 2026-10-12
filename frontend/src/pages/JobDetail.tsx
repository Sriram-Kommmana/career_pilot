import { useNavigate, useParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { ErrorState } from "@/components/ErrorState";
import { DemoDataBadge } from "@/components/DemoDataBadge";
import { MatchBreakdown } from "@/components/MatchBreakdown";
import {
  formatEmploymentType,
  formatPostedDate,
  formatSalary,
  formatWorkArrangement,
} from "@/lib/format";
import { MapPin, Building2, ExternalLink, ArrowLeft } from "lucide-react";
import { useState } from "react";
import type { ApplicationDetailOut, JobDetailOut } from "@/types/api";

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [startError, setStartError] = useState<string | null>(null);
  const [existingApplicationId, setExistingApplicationId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<JobDetailOut>(`/jobs/${jobId}`),
    enabled: !!jobId,
  });

  const startApplication = useMutation({
    mutationFn: () => api.post<ApplicationDetailOut>("/applications", { job_id: jobId }),
    onSuccess: (application) => {
      queryClient.invalidateQueries({ queryKey: ["tracker"] });
      navigate(`/applications/${application.id}`);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "DUPLICATE_APPLICATION") {
        const details = error.details as { application_id?: string } | undefined;
        setExistingApplicationId(details?.application_id ?? null);
        setStartError(error.message);
        return;
      }
      setStartError(error instanceof ApiError ? error.message : "Could not start this application.");
    },
  });

  if (query.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  }
  if (!query.data) return null;

  const { job, description, requirements, responsibilities, match, duplicates } = query.data;
  const salary = job.salary
    ? formatSalary(job.salary.minimum, job.salary.maximum, job.salary.currency, job.salary.period)
    : null;

  return (
    <div className="space-y-6">
      <Link
        to="/jobs"
        className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back to job feed
      </Link>

      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-slate-900">{job.title}</h1>
            {job.is_demo_data && <DemoDataBadge />}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-slate-600">
            <span className="flex items-center gap-1">
              <Building2 className="h-4 w-4" /> {job.company.name}
            </span>
            <span className="flex items-center gap-1">
              <MapPin className="h-4 w-4" /> {job.location_raw ?? "Location not stated"}
            </span>
            <span>{formatWorkArrangement(job.work_arrangement)}</span>
            <span>{formatEmploymentType(job.employment_type)}</span>
          </div>
          <p className="mt-1 text-sm text-slate-400">
            {formatPostedDate(job.posted_at, job.posted_at_precision)}
            {salary && ` · ${salary}`} · Source: {job.source}
          </p>
        </div>
        <div className="flex flex-shrink-0 flex-col items-end gap-2">
          <Button
            onClick={() => startApplication.mutate()}
            isLoading={startApplication.isPending}
          >
            Start application
          </Button>
          <a
            href={job.application_url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"
          >
            View original posting <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </div>

      {startError && (
        <Alert variant={existingApplicationId ? "warning" : "danger"}>
          {startError}
          {existingApplicationId && (
            <>
              {" "}
              <Link
                to={`/applications/${existingApplicationId}`}
                className="font-medium underline"
              >
                Open it
              </Link>
              .
            </>
          )}
        </Alert>
      )}

      {duplicates.length > 0 && (
        <Alert variant="info" title="Also listed elsewhere">
          This role also appears on {duplicates.length} other source
          {duplicates.length > 1 ? "s" : ""}:{" "}
          {duplicates.map((d, i) => (
            <span key={d.id}>
              {i > 0 && ", "}
              <a href={d.application_url} target="_blank" rel="noreferrer" className="underline">
                {d.source}
              </a>
            </span>
          ))}
        </Alert>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {description && (
            <Card>
              <CardHeader>
                <CardTitle>Description</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="whitespace-pre-wrap text-sm text-slate-700">{description}</p>
              </CardContent>
            </Card>
          )}

          {requirements.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Requirements</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="list-inside list-disc space-y-1 text-sm text-slate-700">
                  {requirements.map((req) => (
                    <li key={req.id}>
                      {req.text}
                      {req.is_optional && (
                        <span className="ml-2 text-xs text-slate-400">(preferred)</span>
                      )}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {responsibilities.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Responsibilities</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="list-inside list-disc space-y-1 text-sm text-slate-700">
                  {responsibilities.map((item, idx) => <li key={idx}>{item}</li>)}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>

        <div>
          <Card>
            <CardHeader>
              <CardTitle>Match explanation</CardTitle>
            </CardHeader>
            <CardContent>
              {match ? (
                <MatchBreakdown match={match} />
              ) : (
                <p className="text-sm text-slate-500">
                  Not yet analysed against your profile.
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
