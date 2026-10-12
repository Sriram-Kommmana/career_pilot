import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import { Landing } from "@/pages/Landing";
import { Dashboard } from "@/pages/Dashboard";
import { JobFeed } from "@/pages/JobFeed";
import { JobDetail } from "@/pages/JobDetail";
import { Profile } from "@/pages/Profile";
import { Resumes } from "@/pages/Resumes";
import { Tracker } from "@/pages/Tracker";
import { ApplicationDetail } from "@/pages/ApplicationDetail";
import { Notifications } from "@/pages/Notifications";
import { AgentActivity } from "@/pages/AgentActivity";
import { Settings } from "@/pages/Settings";
import { NotFound } from "@/pages/NotFound";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/login" element={<Landing />} />

      <Route
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/jobs" element={<JobFeed />} />
        <Route path="/jobs/:jobId" element={<JobDetail />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/resumes" element={<Resumes />} />
        <Route path="/tracker" element={<Tracker />} />
        <Route path="/applications/:applicationId" element={<ApplicationDetail />} />
        <Route path="/notifications" element={<Notifications />} />
        <Route path="/activity" element={<AgentActivity />} />
        <Route path="/settings" element={<Settings />} />
      </Route>

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
