import { Link } from "react-router-dom";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DemoDataBadge } from "@/components/DemoDataBadge";
import {
  formatEmploymentType,
  formatPostedDate,
  formatSalary,
  formatScore,
  formatWorkArrangement,
} from "@/lib/format";
import { MapPin, Building2, Layers } from "lucide-react";
import type { JobFeedItemOut } from "@/types/api";

export function JobCard({ item }: { item: JobFeedItemOut }) {
  const { job, match } = item;
  const salary = job.salary
    ? formatSalary(job.salary.minimum, job.salary.maximum, job.salary.currency, job.salary.period)
    : null;

  return (
    <Link to={`/jobs/${job.id}`} className="block">
      <Card className="transition-shadow hover:shadow-md">
        <CardContent className="p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h3 className="truncate text-sm font-semibold text-slate-900">
                  {job.title}
                </h3>
                {job.is_demo_data && <DemoDataBadge />}
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-sm text-slate-600">
                <Building2 className="h-3.5 w-3.5 flex-shrink-0" />
                <span className="truncate">{job.company.name}</span>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <span className="flex items-center gap-1">
                  <MapPin className="h-3 w-3" />
                  {job.location_raw ?? "Location not stated"}
                </span>
                <span>·</span>
                <span>{formatWorkArrangement(job.work_arrangement)}</span>
                <span>·</span>
                <span>{formatEmploymentType(job.employment_type)}</span>
                {job.source_count > 1 && (
                  <>
                    <span>·</span>
                    <span className="flex items-center gap-1">
                      <Layers className="h-3 w-3" />
                      {job.source_count} sources
                    </span>
                  </>
                )}
              </div>
              <p className="mt-2 text-xs text-slate-400">
                {formatPostedDate(job.posted_at, job.posted_at_precision)}
                {salary && ` · ${salary}`}
              </p>
            </div>
            <div className="flex flex-shrink-0 flex-col items-end gap-1">
              {match ? (
                match.passed_hard_filters ? (
                  <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-50 text-sm font-bold text-brand-700">
                    {formatScore(match.score)}
                  </div>
                ) : (
                  <Badge variant="outline">Filtered out</Badge>
                )
              ) : (
                <Badge variant="outline">Not ranked</Badge>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
