import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth, authErrorMessage } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { ShieldCheck, Sparkles, KanbanSquare } from "lucide-react";

export function Landing() {
  const { isAuthenticated, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (isAuthenticated) {
    const state = location.state as { from?: { pathname?: string } } | null;
    const from = state?.from?.pathname ?? "/dashboard";
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password, fullName);
      }
      navigate("/dashboard");
    } catch (err) {
      setError(authErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen">
      <div className="hidden w-1/2 flex-col justify-between bg-brand-700 p-12 text-white lg:flex">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-sm font-bold text-brand-700">
            C
          </div>
          <span className="text-lg font-semibold">CareerPilot</span>
        </div>
        <div>
          <h1 className="text-3xl font-bold leading-tight">
            Automate the search.
            <br />
            You stay in control of every send.
          </h1>
          <div className="mt-8 space-y-5">
            <Feature
              icon={Sparkles}
              title="Transparent matching"
              text="Every score comes with the component breakdown and the evidence behind it — never a black box."
            />
            <Feature
              icon={KanbanSquare}
              title="A real tracker"
              text="Discovered, prepared, awaiting your approval, submitted — one board, always accurate."
            />
            <Feature
              icon={ShieldCheck}
              title="Nothing sends itself"
              text="An application is only ever submitted after your explicit, informed approval of the exact form."
            />
          </div>
        </div>
        <p className="text-xs text-brand-200">
          Phase 1 demo build — job data shown is labelled sample data, not live postings.
        </p>
      </div>

      <div className="flex w-full flex-col items-center justify-center bg-slate-50 p-8 lg:w-1/2">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="mb-2 flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
              C
            </div>
            <h1 className="text-xl font-bold text-slate-900">CareerPilot</h1>
          </div>

          <h2 className="text-xl font-semibold text-slate-900">
            {mode === "login" ? "Sign in" : "Create your account"}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {mode === "login"
              ? "Welcome back. Enter your details to continue."
              : "A demo account? Try demo@example.com / careerpilot-demo-2026"}
          </p>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            {mode === "register" && (
              <div className="space-y-1.5">
                <Label htmlFor="fullName">Full name (optional)</Label>
                <Input
                  id="fullName"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  autoComplete="name"
                />
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                required
                minLength={mode === "register" ? 10 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
              {mode === "register" && (
                <p className="text-xs text-slate-400">At least 10 characters.</p>
              )}
            </div>

            {error && <Alert variant="danger">{error}</Alert>}

            <Button type="submit" className="w-full" isLoading={isSubmitting}>
              {mode === "login" ? "Sign in" : "Create account"}
            </Button>
          </form>

          <p className="mt-5 text-center text-sm text-slate-500">
            {mode === "login" ? "New to CareerPilot?" : "Already have an account?"}{" "}
            <button
              type="button"
              className="font-medium text-brand-600 hover:underline"
              onClick={() => {
                setMode(mode === "login" ? "register" : "login");
                setError(null);
              }}
            >
              {mode === "login" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </div>
      </div>
    </div>
  );
}

function Feature({
  icon: Icon,
  title,
  text,
}: {
  icon: typeof Sparkles;
  title: string;
  text: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-white/10">
        <Icon className="h-4 w-4" aria-hidden="true" />
      </div>
      <div>
        <p className="text-sm font-semibold">{title}</p>
        <p className="text-sm text-brand-200">{text}</p>
      </div>
    </div>
  );
}
