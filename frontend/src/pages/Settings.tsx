import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";
import { useNavigate } from "react-router-dom";
import type { UserOut } from "@/types/api";

export function Settings() {
  const { logout } = useAuth();
  const navigate = useNavigate();

  const meQuery = useQuery({
    queryKey: ["auth-me"],
    queryFn: () => api.get<UserOut>("/auth/me"),
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Settings</h1>
        <p className="mt-1 text-sm text-slate-500">Account and runtime configuration.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent>
          {meQuery.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : meQuery.data ? (
            <dl className="grid grid-cols-[120px_1fr] gap-y-2 text-sm">
              <dt className="text-slate-500">Email</dt>
              <dd className="text-slate-900">{meQuery.data.email}</dd>
              <dt className="text-slate-500">Member since</dt>
              <dd className="text-slate-900">{formatDateTime(meQuery.data.created_at)}</dd>
              <dt className="text-slate-500">Last sign-in</dt>
              <dd className="text-slate-900">{formatDateTime(meQuery.data.last_login_at)}</dd>
            </dl>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Notification channels</CardTitle>
          <CardDescription>
            In-app notifications are active. Email and Telegram are modular adapters
            planned for a later phase.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Badge variant="success">In-app: active</Badge>
          <Badge variant="outline">Email: not yet available</Badge>
          <Badge variant="outline">Telegram: not yet available</Badge>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>AI provider</CardTitle>
          <CardDescription>
            Matching and scoring are fully deterministic — no language model is
            involved. A configurable LLM provider for drafting prose (cover letters,
            explanations) arrives in a later phase and will always require an
            explicit, paid opt-in.
          </CardDescription>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sign out</CardTitle>
        </CardHeader>
        <CardContent>
          <Button
            variant="outline"
            onClick={() => {
              logout();
              navigate("/login");
            }}
          >
            Sign out of this device
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
