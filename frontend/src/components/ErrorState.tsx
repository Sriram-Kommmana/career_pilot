import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message =
    error instanceof ApiError
      ? error.message
      : "Something went wrong while loading this page.";
  const correlationId = error instanceof ApiError ? error.correlationId : undefined;

  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-red-200 bg-red-50 px-6 py-12 text-center">
      <AlertTriangle className="mb-3 h-10 w-10 text-red-500" aria-hidden="true" />
      <p className="text-sm font-medium text-red-900">{message}</p>
      {correlationId && (
        <p className="mt-1 text-xs text-red-600">Reference: {correlationId}</p>
      )}
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
