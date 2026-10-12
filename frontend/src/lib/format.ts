/**
 * Formatting helpers.
 *
 * `formatPostedDate` is the one that matters most: a posting with an unknown date
 * must never be rendered as "posted recently". It renders exactly what the backend
 * told us, including "posting date not provided".
 */
import type { PostedAtPrecision, WorkArrangement, EmploymentType } from "@/types/api";

export function formatPostedDate(
  postedAt: string | null,
  precision: PostedAtPrecision,
): string {
  if (precision === "UNKNOWN" || !postedAt) {
    return "Posting date not provided";
  }
  const date = new Date(postedAt);
  const formatted = date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  return precision === "DATE_ONLY" ? formatted : `${formatted} (exact)`;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatRelativeObserved(lastObservedAt: string): string {
  const diffMs = Date.now() - new Date(lastObservedAt).getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  if (diffHours < 1) return "seen just now";
  if (diffHours < 24) return `seen ${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `seen ${diffDays}d ago`;
}

export function formatSalary(
  min: number | null,
  max: number | null,
  currency: string | null,
  period: string | null,
): string | null {
  if (min === null && max === null) return null;
  // A fixed locale, not the viewer's: a salary figure must group digits the same
  // way for every reader, rather than switching between "120,000" and "1,20,000"
  // depending on whose machine renders it.
  const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 0 });
  const amount =
    min !== null && max !== null
      ? `${fmt(min)}–${fmt(max)}`
      : fmt((min ?? max) as number);
  const suffix = period ? ` / ${period}` : "";
  return `${currency ? `${currency} ` : ""}${amount}${suffix}`;
}

export function formatScore(score: number | null): string {
  if (score === null) return "Not ranked";
  return `${Math.round(score * 100)}%`;
}

export function formatWorkArrangement(value: WorkArrangement): string {
  const labels: Record<WorkArrangement, string> = {
    REMOTE: "Remote",
    HYBRID: "Hybrid",
    ONSITE: "On-site",
    UNKNOWN: "Location type unknown",
  };
  return labels[value];
}

export function formatEmploymentType(value: EmploymentType): string {
  const labels: Record<EmploymentType, string> = {
    FULL_TIME: "Full-time",
    PART_TIME: "Part-time",
    INTERNSHIP: "Internship",
    CONTRACT: "Contract",
    TEMPORARY: "Temporary",
    APPRENTICESHIP: "Apprenticeship",
    UNKNOWN: "Type unknown",
  };
  return labels[value];
}

export function formatStateLabel(state: string): string {
  return state
    .split("_")
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(" ");
}

export function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function initials(name: string | null | undefined, fallback = "?"): string {
  if (!name?.trim()) return fallback;
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? parts[parts.length - 1]?.[0] ?? "" : "";
  return (first + last).toUpperCase() || fallback;
}
