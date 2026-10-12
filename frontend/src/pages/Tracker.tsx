import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import { DemoDataBadge } from "@/components/DemoDataBadge";
import { formatDateTime } from "@/lib/format";
import { KanbanSquare } from "lucide-react";
import type { TrackerBoardOut, ApplicationSummaryOut } from "@/types/api";

const COLUMN_LABELS: Record<string, string> = {
  saved: "Saved",
  preparing: "Preparing",
  awaiting_input: "Awaiting your input",
  awaiting_approval: "Awaiting your approval",
  submitting: "Submitting",
  submitted: "Submitted",
  needs_reconciliation: "Needs reconciliation",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function Tracker() {
  const query = useQuery({
    queryKey: ["tracker"],
    queryFn: () => api.get<TrackerBoardOut>("/applications/tracker"),
  });

  if (query.isLoading) {
    return (
      <div className="flex gap-4 overflow-x-auto">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-96 w-64 flex-shrink-0" />
        ))}
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  }
  if (!query.data) return null;

  const total = Object.values(query.data.columns).reduce((sum, c) => sum + c.length, 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Application tracker</h1>
        <p className="mt-1 text-sm text-slate-500">
          Every application you've started, grouped by where it stands.
        </p>
      </div>

      {total === 0 ? (
        <EmptyState
          icon={KanbanSquare}
          title="No applications yet"
          description="Start one from a job's detail page to see it appear here."
        />
      ) : (
        <div className="scroll-thin flex gap-4 overflow-x-auto pb-4">
          {query.data.column_order.map((column) => {
            const items = query.data!.columns[column] ?? [];
            return (
              <div key={column} className="w-72 flex-shrink-0">
                <div className="mb-2 flex items-center justify-between px-1">
                  <h2 className="text-sm font-semibold text-slate-700">
                    {COLUMN_LABELS[column] ?? column}
                  </h2>
                  <span className="text-xs text-slate-400">{items.length}</span>
                </div>
                <div className="scroll-thin flex max-h-[calc(100vh-220px)] flex-col gap-2 overflow-y-auto">
                  {items.map((application) => (
                    <TrackerCard key={application.id} application={application} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function TrackerCard({ application }: { application: ApplicationSummaryOut }) {
  return (
    <Link to={`/applications/${application.id}`}>
      <Card className="transition-shadow hover:shadow-md">
        <CardContent className="p-3.5">
          <div className="flex items-start justify-between gap-2">
            <p className="truncate text-sm font-medium text-slate-900">
              {application.job.title}
            </p>
            {application.job.is_demo_data && <DemoDataBadge />}
          </div>
          <p className="mt-0.5 truncate text-xs text-slate-500">
            {application.job.company.name}
          </p>
          {application.unresolved_interventions.length > 0 && (
            <p className="mt-2 text-xs font-medium text-amber-600">
              {application.unresolved_interventions.join(", ")}
            </p>
          )}
          <p className="mt-2 text-xs text-slate-400">
            Updated {formatDateTime(application.updated_at)}
          </p>
        </CardContent>
      </Card>
    </Link>
  );
}
