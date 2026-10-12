import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Landing } from "@/pages/Landing";
import { ApiError } from "@/lib/api";
import * as authModule from "@/lib/auth";

vi.mock("@/lib/auth", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth")>("@/lib/auth");
  return {
    ...actual,
    useAuth: vi.fn(),
  };
});

const useAuthMock = authModule.useAuth as unknown as ReturnType<typeof vi.fn>;

describe("Landing", () => {
  beforeEach(() => {
    useAuthMock.mockReset();
  });

  it("redirects to the dashboard when already authenticated", () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: true,
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      user: null,
      isLoading: false,
    });
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Landing />
      </MemoryRouter>,
    );
    // Navigate renders nothing itself; the login form must not be present.
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
  });

  it("shows the login form by default, requiring email and password", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: false,
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      user: null,
      isLoading: false,
    });
    render(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeRequired();
    expect(screen.getByLabelText("Password")).toBeRequired();
  });

  it("shows a uniform error message when login fails, without leaking which part was wrong", async () => {
    const login = vi.fn().mockRejectedValue(
      new ApiError(401, {
        code: "NOT_AUTHENTICATED",
        message: "Incorrect email or password.",
        correlation_id: "abc123",
      }),
    );
    useAuthMock.mockReturnValue({
      isAuthenticated: false,
      login,
      register: vi.fn(),
      logout: vi.fn(),
      user: null,
      isLoading: false,
    });
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>,
    );

    await user.type(screen.getByLabelText("Email"), "someone@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByText("Incorrect email or password.")).toBeInTheDocument();
    });
    expect(login).toHaveBeenCalledWith("someone@example.com", "wrong-password");
  });

  it("switches to the registration form and enforces the minimum password length", async () => {
    useAuthMock.mockReturnValue({
      isAuthenticated: false,
      login: vi.fn(),
      register: vi.fn(),
      logout: vi.fn(),
      user: null,
      isLoading: false,
    });
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <Landing />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Create an account" }));
    expect(screen.getByRole("heading", { name: "Create your account" })).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("minLength", "10");
  });
});
