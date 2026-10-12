import { Badge } from "@/components/ui/badge";
import { formatStateLabel } from "@/lib/format";
import type { ApplicationState } from "@/types/api";

/**
 * Colour-codes an application state by what it means for the user to do next,
 * not just by category — "needs your input" and "needs your approval" both read
 * as attention-worthy, while terminal states read as settled.
 */
const VARIANT_BY_STATE: Record<
  ApplicationState,
  "default" | "brand" | "success" | "warning" | "danger" | "outline"
> = {
  DISCOVERED: "outline",
  MATCHED: "outline",
  SELECTED: "default",
  PREPARING: "brand",
  READY: "brand",
  FILLING: "brand",
  VERIFYING: "brand",
  WAITING_FOR_USER_INPUT: "warning",
  WAITING_FOR_APPROVAL: "warning",
  SUBMITTING: "brand",
  SUBMITTED: "success",
  SUBMITTED_UNCERTAIN: "warning",
  FAILED: "danger",
  CANCELLED: "default",
};

export function StatusBadge({ state }: { state: ApplicationState }) {
  return <Badge variant={VARIANT_BY_STATE[state]}>{formatStateLabel(state)}</Badge>;
}
