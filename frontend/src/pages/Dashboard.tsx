import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { formatScore } from "@/lib/format";
import { Briefcase, KanbanSquare, ArrowRight } from "lucide-react";
import type { Page, JobFeedItemOut, TrackerBoardOut } from "@/types/api";

export function Dashboard() {
  const jobsQuery = useQuery({
    queryKey: ["jobs", { limit: 5 }],
    queryFn: () => api.get<Page<JobFeedItemOut>>("/jobs", { limit: 5 }),
  });
  const trackerQuery = useQuery({
    queryKey: ["tracker"],
    queryFn: () => api.get<TrackerBoardOut>("/applications/tracker"),
  });

  const totalApplications = trackerQuery.data
    ? Object.values(trackerQuery.data.columns).reduce((sum, col) => sum + col.length, 0)
    : null;
  const awaitingAttention = trackerQuery.data
    ? (trackerQuery.data.columns.awaiting_input?.length ?? 0) +
      (trackerQuery.data.columns.awaiting_approval?.length ?? 0)
    : null;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
        <p className="mt-1 text-sm text-slate-500">
          An overview of your job search. Match scores are ranking heuristics — read
          the breakdown, not just the number.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Jobs in your feed"
          value={jobsQuery.data?.total}
          isLoading={jobsQuery.isLoading}
        />
        <StatCard
          label="Applications tracked"
          value={totalApplications}
          isLoading={trackerQuery.isLoading}
        />
        <StatCard
          label="Need your attention"
          value={awaitingAttention}
          isLoading={trackerQuery.isLoading}
          highlight={!!awaitingAttention}
        />
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Top matches</CardTitle>
          <Link
            to="/jobs"
            className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-100"
          >
            View all <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </CardHeader>
        <CardContent>
          {jobsQuery.isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-14 w-full" />
              ))}
            </div>
          ) : jobsQuery.isError ? (
            <ErrorState error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />
          ) : jobsQuery.data?.items.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-500">
              No jobs in your feed yet.
            </p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {jobsQuery.data?.items.map(({ job, match }) => (
                <li key={job.id} className="flex items-center justify-between py-3">
                  <Link
                    to={`/jobs/${job.id}`}
                    className="min-w-0 flex-1 truncate text-sm font-medium text-slate-900 hover:text-brand-600"
                  >
                    {job.title}
                    <span className="ml-2 font-normal text-slate-500">
                      at {job.company.name}
                    </span>
                  </Link>
                  <span className="ml-4 flex-shrink-0 text-sm font-semibold text-brand-700">
                    {formatScore(match?.score ?? null)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <QuickLinkCard
          icon={Briefcase}
          title="Browse the job feed"
          description="Filter jobs, see match explanations, and prepare applications."
          to="/jobs"
        />
        <QuickLinkCard
          icon={KanbanSquare}
          title="Open your tracker"
          description="See every application by stage, from saved through submitted."
          to="/tracker"
        />
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  isLoading,
  highlight,
}: {
  label: string;
  value: number | null | undefined;
  isLoading: boolean;
  highlight?: boolean;
}) {
  return (
    <Card>
      <CardContent className="p-5">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
          {label}
        </p>
        {isLoading ? (
          <Skeleton className="mt-2 h-8 w-12" />
        ) : (
          <p
            className={`mt-1 text-2xl font-bold ${highlight && value ? "text-amber-600" : "text-slate-900"}`}
          >
            {value ?? "—"}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function QuickLinkCard({
  icon: Icon,
  title,
  description,
  to,
}: {
  icon: typeof Briefcase;
  title: string;
  description: string;
  to: string;
}) {
  return (
    <Link to={to}>
      <Card className="h-full transition-shadow hover:shadow-md">
        <CardContent className="flex items-start gap-4 p-5">
          <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-brand-50">
            <Icon className="h-5 w-5 text-brand-600" />
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-900">{title}</p>
            <p className="mt-0.5 text-sm text-slate-500">{description}</p>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
