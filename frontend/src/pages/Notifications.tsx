import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";
import { formatDateTime } from "@/lib/format";
import { Bell, BellOff } from "lucide-react";
import type { NotificationListOut } from "@/types/api";

export function Notifications() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<NotificationListOut>("/notifications", { limit: 50 }),
  });

  const markReadMutation = useMutation({
    mutationFn: (id: string) => api.post(`/notifications/${id}/read`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });
  const markAllReadMutation = useMutation({
    mutationFn: () => api.post("/notifications/read-all"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
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
  const data = query.data;
  if (!data) return null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Notifications</h1>
          <p className="mt-1 text-sm text-slate-500">
            {data.unread > 0 ? `${data.unread} unread` : "All caught up"}
          </p>
        </div>
        {data.unread > 0 && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => markAllReadMutation.mutate()}
            isLoading={markAllReadMutation.isPending}
          >
            Mark all read
          </Button>
        )}
      </div>

      {data.items.length === 0 ? (
        <EmptyState
          icon={BellOff}
          title="No notifications yet"
          description="You'll see job matches above your threshold and application updates here."
        />
      ) : (
        <div className="space-y-2">
          {data.items.map((notification) => (
            <Card
              key={notification.id}
              className={notification.read_at ? "opacity-60" : undefined}
            >
              <CardContent className="flex items-start justify-between gap-4 p-4">
                <div className="flex items-start gap-3">
                  <Bell className="mt-0.5 h-4 w-4 flex-shrink-0 text-brand-500" />
                  <div>
                    {notification.link ? (
                      <Link
                        to={notification.link}
                        className="text-sm font-medium text-slate-900 hover:text-brand-600"
                      >
                        {notification.title}
                      </Link>
                    ) : (
                      <p className="text-sm font-medium text-slate-900">
                        {notification.title}
                      </p>
                    )}
                    {notification.body && (
                      <p className="mt-0.5 text-sm text-slate-500">{notification.body}</p>
                    )}
                    <p className="mt-1 text-xs text-slate-400">
                      {formatDateTime(notification.created_at)}
                    </p>
                  </div>
                </div>
                {!notification.read_at && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => markReadMutation.mutate(notification.id)}
                  >
                    Mark read
                  </Button>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
