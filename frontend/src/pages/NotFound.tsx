import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-50">
      <p className="text-5xl font-bold text-slate-300">404</p>
      <p className="text-sm text-slate-500">That page doesn't exist.</p>
      <Link
        to="/dashboard"
        className="inline-flex h-10 items-center justify-center rounded-lg bg-brand-600 px-4 text-sm font-medium text-white hover:bg-brand-700"
      >
        Go to dashboard
      </Link>
    </div>
  );
}
