import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { PilotInterestForm } from "@/app/pilot/pilot-interest-form";
import { server } from "./setup";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

describe("PilotInterestForm", () => {
  it("submits a valid pilot request to /api/v1/pilot-interest and shows a neutral confirmation", async () => {
    const submissions: unknown[] = [];
    server.use(
      http.post("/api/v1/pilot-interest", async ({ request }) => {
        submissions.push(await request.json());
        return HttpResponse.json({ status: "received" }, { status: 202 });
      }),
    );

    await act(async () => {
      render(<PilotInterestForm />);
    });

    fireEvent.change(screen.getByLabelText(/Full name/i), {
      target: { value: "Jamie Pilot" },
    });
    fireEvent.change(screen.getByLabelText(/Email/i), {
      target: { value: "jamie@example.com" },
    });
    fireEvent.change(screen.getByLabelText(/^Role$/), {
      target: { value: "trainer" },
    });
    fireEvent.change(screen.getByLabelText(/Organization/i), {
      target: { value: "Central Rec Basketball" },
    });
    fireEvent.change(screen.getByLabelText(/Anything else/i), {
      target: { value: "Looking for alpha access for a 12-game season." },
    });
    fireEvent.click(screen.getByTestId("pilot-submit"));

    await waitFor(() => {
      expect(submissions.length).toBe(1);
    });
    expect(submissions[0]).toEqual({
      full_name: "Jamie Pilot",
      email: "jamie@example.com",
      role: "trainer",
      organization: "Central Rec Basketball",
      message: "Looking for alpha access for a 12-game season.",
    });

    // Confirmation copy is neutral; never echoes back the submission.
    const success = await screen.findByTestId("pilot-success");
    expect(success).toBeInTheDocument();
    expect(success.textContent ?? "").not.toMatch(/Jamie Pilot/);
    expect(success.textContent ?? "").not.toMatch(/jamie@example.com/);
  });

  it("surfaces a friendly 429 message without revealing rate-limit internals", async () => {
    server.use(
      http.post("/api/v1/pilot-interest", () =>
        HttpResponse.json(
          {
            error: { code: "RATE_LIMITED", message: "Too many attempts." },
          },
          { status: 429 },
        ),
      ),
    );

    await act(async () => {
      render(<PilotInterestForm />);
    });

    fireEvent.change(screen.getByLabelText(/Full name/i), {
      target: { value: "Probe" },
    });
    fireEvent.change(screen.getByLabelText(/Email/i), {
      target: { value: "probe@example.com" },
    });
    fireEvent.click(screen.getByTestId("pilot-submit"));

    expect(
      await screen.findByText(/Too many requests from this network/i),
    ).toBeInTheDocument();
  });
});
