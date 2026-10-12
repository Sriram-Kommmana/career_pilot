import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import { formatDateTime } from "@/lib/format";
import { Activity } from "lucide-react";
import type { Page, AgentRunOut } from "@/types/api";

/**
 * Agent run history. Shows a concise decision summary and tool-call counts per
 * run — never chain-of-thought or raw prompts, which the backend never stores or
 * returns here in the first place.
 *
 * Phase 1 is fully deterministic (no connectors, no browser agent), so this list
 * is expected to be empty until Phase 2 onward starts recording runs. It is wired
 * to the real endpoint rather than mocked, so it will populate automatically once
 * those phases ship.
 */
export function AgentActivity() {
  const query = useQuery({
    queryKey: ["agent-runs"],
    queryFn: () => api.get<Page<AgentRunOut>>("/agent-runs", { limit: 50 }),
  });

  if (query.isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Agent activity</h1>
        <p className="mt-1 text-sm text-slate-500">
          What the agent actually did, as concise decision summaries — never its
          internal reasoning or prompts.
        </p>
      </div>

      {query.data?.items.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No agent runs yet"
          description="Phase 1 is fully deterministic — no connectors or browser agent run automatically yet. This page will populate from Phase 2 onward."
        />
      ) : (
        <div className="space-y-2">
          {query.data?.items.map((run) => (
            <Card key={run.id}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium capitalize text-slate-900">
                    {run.kind.replace(/_/g, " ")}
                  </span>
                  <span className="text-xs text-slate-400">
                    {formatDateTime(run.started_at)}
                  </span>
                </div>
                {run.summary && (
                  <p className="mt-1 text-sm text-slate-600">{run.summary}</p>
                )}
                <p className="mt-1 text-xs text-slate-400">status: {run.status}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
