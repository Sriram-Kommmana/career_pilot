/**
 * Authentication state.
 *
 * A thin context around the bearer token and the current user. Authorisation
 * itself is never trusted to anything client-side: every protected request still
 * carries the token to the backend, which re-checks ownership on every call. This
 * context only decides what the UI shows — it is not a security boundary.
 */
/* eslint-disable react-refresh/only-export-components -- the standard context
 * pattern: the provider, its hook, and a small error-formatting helper belong
 * together. Losing fast refresh on this one file is a minor DX cost. */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getToken, setToken } from "@/lib/api";
import type { TokenResponse, UserOut } from "@/types/api";

interface AuthContextValue {
  user: UserOut | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null);
  // Only enter the loading state when there is actually a token to verify, so the
  // no-token path never needs a synchronous setState inside the effect body below.
  const [isLoading, setIsLoading] = useState<boolean>(() => !!getToken());
  const queryClient = useQueryClient();

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    api
      .get<UserOut>("/auth/me")
      .then(setUser)
      .catch(() => {
        // The stored token is no longer valid (expired, or the account changed).
        setToken(null);
      })
      .finally(() => setIsLoading(false));
  }, []);

  async function handleAuthResponse(response: TokenResponse) {
    setToken(response.access_token);
    const me = await api.get<UserOut>("/auth/me");
    setUser(me);
  }

  async function login(email: string, password: string) {
    const response = await api.post<TokenResponse>("/auth/login", { email, password });
    await handleAuthResponse(response);
  }

  async function register(email: string, password: string, fullName?: string) {
    const response = await api.post<TokenResponse>("/auth/register", {
      email,
      password,
      full_name: fullName || undefined,
    });
    await handleAuthResponse(response);
  }

  function logout() {
    setToken(null);
    setUser(null);
    queryClient.clear();
  }

  return (
    <AuthContext.Provider
      value={{ user, isLoading, isAuthenticated: !!user, login, register, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

/** A readable message for a login/register failure, without leaking which part was wrong. */
export function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "VALIDATION_ERROR") {
      const details = error.details as { errors?: { message?: string }[] } | undefined;
      return details?.errors?.[0]?.message ?? error.message;
    }
    return error.message;
  }
  return "Something went wrong. Please try again.";
}
