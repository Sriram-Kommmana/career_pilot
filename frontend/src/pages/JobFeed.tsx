import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { JobCard } from "@/components/JobCard";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Search, Briefcase, ChevronLeft, ChevronRight } from "lucide-react";
import type { Page, JobFeedItemOut } from "@/types/api";

const PAGE_SIZE = 10;

export function JobFeed() {
  const [search, setSearch] = useState("");
  const [workArrangement, setWorkArrangement] = useState("");
  const [employmentType, setEmploymentType] = useState("");
  const [offset, setOffset] = useState(0);

  const query = useQuery({
    queryKey: ["jobs", { search, workArrangement, employmentType, offset }],
    queryFn: () =>
      api.get<Page<JobFeedItemOut>>("/jobs", {
        search: search || undefined,
        work_arrangement: workArrangement || undefined,
        employment_type: employmentType || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Job feed</h1>
        <p className="mt-1 text-sm text-slate-500">
          Jobs discovered from configured sources, ranked against your profile.
          Rows labelled "Demo data" are seeded sample postings, not live jobs.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-[220px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input
            placeholder="Search title or description..."
            className="pl-9"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <Select
          className="w-auto"
          value={workArrangement}
          onChange={(e) => {
            setWorkArrangement(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">Any work arrangement</option>
          <option value="REMOTE">Remote</option>
          <option value="HYBRID">Hybrid</option>
          <option value="ONSITE">On-site</option>
        </Select>
        <Select
          className="w-auto"
          value={employmentType}
          onChange={(e) => {
            setEmploymentType(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">Any employment type</option>
          <option value="FULL_TIME">Full-time</option>
          <option value="PART_TIME">Part-time</option>
          <option value="INTERNSHIP">Internship</option>
          <option value="CONTRACT">Contract</option>
        </Select>
      </div>

      {query.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : query.data?.items.length === 0 ? (
        <EmptyState
          icon={Briefcase}
          title="No jobs match your filters"
          description="Try widening your search or clearing a filter."
        />
      ) : (
        <>
          <div className="space-y-3">
            {query.data?.items.map((item) => (
              <JobCard key={item.job.id} item={item} />
            ))}
          </div>

          <div className="flex items-center justify-between pt-2">
            <p className="text-sm text-slate-500">
              {query.data
                ? `Showing ${offset + 1}–${Math.min(offset + PAGE_SIZE, query.data.total)} of ${query.data.total}`
                : null}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeft className="h-4 w-4" /> Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!query.data || offset + PAGE_SIZE >= query.data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
