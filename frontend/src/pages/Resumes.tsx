import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { formatFileSize } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import { FileText, Upload, Trash2, RefreshCw, CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import type {
  ResumeOut,
  ExtractionProposalOut,
  ExtractionItemOut,
  ExtractionDecision,
  ConfirmExtractionResponse,
} from "@/types/api";

const FIELD_LABELS: Record<string, string> = {
  skill: "Skill",
  education: "Education",
  project: "Project",
  experience: "Experience",
  certification: "Certification",
  achievement: "Achievement / summary",
};

export function Resumes() {
  const queryClient = useQueryClient();
  const [reviewingResumeId, setReviewingResumeId] = useState<string | null>(null);

  const resumesQuery = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.get<ResumeOut[]>("/resumes"),
  });

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: ["resumes"] });
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Resumes</h1>
        <p className="mt-1 text-sm text-slate-500">
          Upload a resume, read what CareerPilot found in it, and decide what
          joins your profile. Nothing here is added until you explicitly
          confirm it — see each item's source excerpt before accepting it.
        </p>
      </div>

      <UploadCard onUploaded={invalidate} />

      {resumesQuery.isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}
      {resumesQuery.isError && (
        <ErrorState error={resumesQuery.error} onRetry={() => resumesQuery.refetch()} />
      )}
      {resumesQuery.data && resumesQuery.data.length === 0 && (
        <EmptyState
          icon={FileText}
          title="No resumes uploaded yet"
          description="Upload a PDF, DOCX, or plain text resume above to get started."
        />
      )}
      {resumesQuery.data && resumesQuery.data.length > 0 && (
        <div className="space-y-3">
          {resumesQuery.data.map((resume) => (
            <ResumeRow
              key={resume.id}
              resume={resume}
              isReviewing={reviewingResumeId === resume.id}
              onReview={() => setReviewingResumeId(resume.id)}
              onCloseReview={() => setReviewingResumeId(null)}
              onChanged={invalidate}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

function UploadCard({ onUploaded }: { onUploaded: () => void }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [label, setLabel] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const uploadMutation = useMutation({
    mutationFn: async () => {
      const file = fileInputRef.current?.files?.[0];
      if (!file) throw new Error("Choose a file first.");
      const form = new FormData();
      form.append("file", file);
      if (label.trim()) form.append("label", label.trim());
      form.append("is_default", isDefault ? "true" : "false");
      return api.postForm<ResumeOut>("/resumes", form);
    },
    onSuccess: () => {
      setLabel("");
      setIsDefault(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
      onUploaded();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Upload failed."),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload a resume</CardTitle>
        <CardDescription>PDF, DOCX, or plain text. Up to 5 MB.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setError(null);
            uploadMutation.mutate();
          }}
          className="grid gap-3 sm:grid-cols-[1fr_auto]"
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.txt"
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm file:font-medium"
          />
          <Button type="submit" isLoading={uploadMutation.isPending}>
            <Upload className="h-4 w-4" /> Upload
          </Button>
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Label (optional, e.g. 'Backend-focused resume')"
            className="sm:col-span-2"
          />
          <label className="flex items-center gap-2 text-sm text-slate-600 sm:col-span-2">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Use as my default resume
          </label>
        </form>
        {error && (
          <Alert variant="danger" className="mt-3">
            {error}
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// One resume row
// ---------------------------------------------------------------------------

function statusBadge(resume: ResumeOut) {
  if (resume.extraction_status === "failed") {
    return <Badge variant="danger">Extraction failed</Badge>;
  }
  if (resume.extraction_status === "pending") {
    return <Badge variant="outline">Not extracted yet</Badge>;
  }
  if (resume.extraction_confirmed_at) {
    return <Badge variant="success">Reviewed</Badge>;
  }
  return <Badge variant="warning">Needs your review</Badge>;
}

function ResumeRow({
  resume,
  isReviewing,
  onReview,
  onCloseReview,
  onChanged,
}: {
  resume: ResumeOut;
  isReviewing: boolean;
  onReview: () => void;
  onCloseReview: () => void;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const extractMutation = useMutation({
    mutationFn: () => api.post<ExtractionProposalOut>(`/resumes/${resume.id}/extract`),
    onSuccess: () => {
      onChanged();
      onReview();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Extraction failed."),
  });
  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/resumes/${resume.id}`),
    onSuccess: onChanged,
  });

  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium text-slate-900">{resume.label}</p>
              {resume.is_default && <Badge variant="brand">Default</Badge>}
              {statusBadge(resume)}
            </div>
            <p className="mt-0.5 text-sm text-slate-500">
              {resume.original_filename} &middot; {formatFileSize(resume.size_bytes)}
            </p>
            {resume.extraction_status === "failed" && resume.extraction_error && (
              <p className="mt-1 text-sm text-red-600">{resume.extraction_error}</p>
            )}
          </div>
          <div className="flex flex-shrink-0 items-center gap-2">
            {resume.extraction_status === "pending" && (
              <Button
                size="sm"
                variant="outline"
                isLoading={extractMutation.isPending}
                onClick={() => extractMutation.mutate()}
              >
                Extract
              </Button>
            )}
            {resume.extraction_status === "failed" && (
              <Button
                size="sm"
                variant="outline"
                isLoading={extractMutation.isPending}
                onClick={() => extractMutation.mutate()}
              >
                <RefreshCw className="h-3.5 w-3.5" /> Retry
              </Button>
            )}
            {resume.extraction_status === "succeeded" && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => (isReviewing ? onCloseReview() : onReview())}
              >
                {isReviewing ? "Hide review" : "Review"}
              </Button>
            )}
            <button
              onClick={() => deleteMutation.mutate()}
              className="text-slate-400 hover:text-red-600"
              aria-label={`Delete ${resume.label}`}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
        {error && <Alert variant="danger">{error}</Alert>}
        {isReviewing && <ExtractionReview resumeId={resume.id} onConfirmed={onChanged} />}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Extraction review and confirmation
// ---------------------------------------------------------------------------

function ExtractionReview({
  resumeId,
  onConfirmed,
}: {
  resumeId: string;
  onConfirmed: () => void;
}) {
  const queryClient = useQueryClient();
  const proposalQuery = useQuery({
    queryKey: ["resume-extraction", resumeId],
    queryFn: () => api.get<ExtractionProposalOut>(`/resumes/${resumeId}/extraction`),
  });

  // index -> accepted?  index -> use the existing record instead of adding new?
  const [accepted, setAccepted] = useState<Record<number, boolean>>({});
  const [useExisting, setUseExisting] = useState<Record<number, boolean>>({});
  const [results, setResults] = useState<ConfirmExtractionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const confirmMutation = useMutation({
    mutationFn: (decisions: ExtractionDecision[]) =>
      api.post<ConfirmExtractionResponse>(`/resumes/${resumeId}/confirm-extraction`, {
        decisions,
      }),
    onSuccess: (response) => {
      setResults(response);
      onConfirmed();
      queryClient.invalidateQueries({ queryKey: ["resume-extraction", resumeId] });
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not confirm."),
  });

  if (proposalQuery.isLoading) return <Skeleton className="h-32 w-full" />;
  if (proposalQuery.isError) {
    return <ErrorState error={proposalQuery.error} onRetry={() => proposalQuery.refetch()} />;
  }
  const items = proposalQuery.data?.items ?? [];
  if (items.length === 0) {
    return (
      <p className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">
        Nothing recognisable was found in this file.
      </p>
    );
  }

  function isAccepted(item: ExtractionItemOut): boolean {
    return accepted[item.index] ?? (item.confidence !== "low" && !item.needs_review);
  }

  function handleConfirm() {
    setError(null);
    const decisions: ExtractionDecision[] = items.map((item) => ({
      index: item.index,
      accept: isAccepted(item),
      existing_record_id:
        isAccepted(item) && useExisting[item.index] && item.possible_duplicate_of
          ? item.possible_duplicate_of
          : null,
    }));
    confirmMutation.mutate(decisions);
  }

  return (
    <div className="space-y-3 border-t border-slate-100 pt-4">
      {proposalQuery.data?.unparsed_sections.length ? (
        <Alert variant="warning">
          Found a section CareerPilot could not make sense of:{" "}
          {proposalQuery.data.unparsed_sections.join(", ")}. Add it to your profile by hand if
          it matters.
        </Alert>
      ) : null}
      <ul className="space-y-2">
        {items.map((item) => (
          <ExtractionItemRow
            key={item.index}
            item={item}
            accepted={isAccepted(item)}
            onToggleAccept={(value) => setAccepted((prev) => ({ ...prev, [item.index]: value }))}
            useExisting={useExisting[item.index] ?? false}
            onToggleUseExisting={(value) =>
              setUseExisting((prev) => ({ ...prev, [item.index]: value }))
            }
          />
        ))}
      </ul>
      <div className="flex items-center gap-3">
        <Button onClick={handleConfirm} isLoading={confirmMutation.isPending}>
          Confirm selected items
        </Button>
        <p className="text-sm text-slate-500">
          {items.filter(isAccepted).length} of {items.length} selected
        </p>
      </div>
      {error && <Alert variant="danger">{error}</Alert>}
      {results && (
        <Alert variant="success" title="Confirmed">
          {results.results.filter((r) => r.status === "created").length} added,{" "}
          {results.results.filter((r) => r.status === "updated").length} updated,{" "}
          {results.results.filter((r) => r.status === "skipped").length} already on your
          profile, {results.results.filter((r) => r.status === "rejected").length} skipped.
        </Alert>
      )}
    </div>
  );
}

function confidenceIcon(item: ExtractionItemOut) {
  if (item.needs_review || item.confidence === "low") {
    return <HelpCircle className="h-4 w-4 flex-shrink-0 text-amber-500" aria-label="Needs review" />;
  }
  return <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-emerald-500" aria-label="Looks reliable" />;
}

function itemSummary(item: ExtractionItemOut): string {
  const data = item.data;
  switch (item.field) {
    case "skill":
      return String(data.name ?? "");
    case "education":
      return [data.degree, data.institution].filter(Boolean).join(" — ") || "Education entry";
    case "experience":
      return [data.title, data.company].filter(Boolean).join(" at ") || "Experience entry";
    case "certification":
      return String(data.name ?? "");
    case "project":
      return String(data.name ?? "");
    case "achievement":
      return String(data.text ?? "");
    default:
      return item.source_excerpt;
  }
}

function ExtractionItemRow({
  item,
  accepted,
  onToggleAccept,
  useExisting,
  onToggleUseExisting,
}: {
  item: ExtractionItemOut;
  accepted: boolean;
  onToggleAccept: (value: boolean) => void;
  useExisting: boolean;
  onToggleUseExisting: (value: boolean) => void;
}) {
  return (
    <li className="rounded-lg border border-slate-200 p-3">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={accepted}
          onChange={(e) => onToggleAccept(e.target.checked)}
          className="mt-1"
          aria-label={`Accept ${itemSummary(item)}`}
        />
        <div className="flex-1 space-y-1">
          <div className="flex items-center gap-2">
            {confidenceIcon(item)}
            <Badge variant="outline">{FIELD_LABELS[item.field] ?? item.field}</Badge>
            <p className="text-sm font-medium text-slate-900">{itemSummary(item)}</p>
          </div>
          <p className="text-xs text-slate-500">
            From: &ldquo;{item.source_excerpt}&rdquo;
          </p>
          {item.possible_duplicate_of && (
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={useExisting}
                onChange={(e) => onToggleUseExisting(e.target.checked)}
                disabled={!accepted}
              />
              This looks like something already on your profile — update it instead of
              adding a new entry
            </label>
          )}
        </div>
        {!accepted && <XCircle className="h-4 w-4 flex-shrink-0 text-slate-300" aria-hidden="true" />}
      </div>
    </li>
  );
}
