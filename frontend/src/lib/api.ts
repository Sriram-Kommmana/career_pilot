/**
 * The HTTP client. One small wrapper around fetch, not a generated SDK, so the
 * request/response shape stays visible and easy to reason about.
 *
 * Every failure throws an `ApiError` carrying the backend's error envelope
 * (`code`, `message`, `correlation_id`, `details`), so callers can branch on
 * `error.code` — e.g. show the submission gate's `blockers` from
 * `SUBMISSION_BLOCKED`, or a field-specific message from `VALIDATION_ERROR`.
 */
import type { ErrorEnvelope } from "@/types/api";

const BASE_URL = `${import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"}/api/v1`;

const TOKEN_STORAGE_KEY = "careerpilot.token";

export class ApiError extends Error {
  code: string;
  correlationId: string;
  details: unknown;
  status: number;

  constructor(status: number, envelope: ErrorEnvelope["error"]) {
    super(envelope.message);
    this.name = "ApiError";
    this.status = status;
    this.code = envelope.code;
    this.correlationId = envelope.correlation_id;
    this.details = envelope.details;
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(TOKEN_STORAGE_KEY, token);
    } else {
      localStorage.removeItem(TOKEN_STORAGE_KEY);
    }
  } catch {
    // Browser storage can be unavailable (private mode, blocked cookies). The
    // session simply will not persist across a reload; nothing to crash over.
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
}

function buildUrl(path: string, params?: RequestOptions["params"]): string {
  const url = new URL(`${BASE_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(buildUrl(path, options.params), {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const data: unknown = text ? JSON.parse(text) : undefined;

  if (!response.ok) {
    const envelope = data as ErrorEnvelope | undefined;
    if (envelope?.error) {
      throw new ApiError(response.status, envelope.error);
    }
    throw new ApiError(response.status, {
      code: "UNKNOWN_ERROR",
      message: response.statusText || "Request failed.",
      correlation_id: "",
    });
  }

  return data as T;
}

export const api = {
  get: <T>(path: string, params?: RequestOptions["params"]) =>
    request<T>(path, { method: "GET", params }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export { BASE_URL };
