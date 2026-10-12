import { Badge } from "@/components/ui/badge";
import { FlaskConical } from "lucide-react";

/**
 * The visible marker required by the safety contract: a seeded demo job must
 * never be mistaken for a live posting. Every place a job is rendered checks
 * `is_demo_data` and shows this badge — there is no code path that hides it.
 */
export function DemoDataBadge() {
  return (
    <Badge variant="warning" title="This is seeded sample data, not a live job posting.">
      <FlaskConical className="h-3 w-3" aria-hidden="true" />
      Demo data
    </Badge>
  );
}
