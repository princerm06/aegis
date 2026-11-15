import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CommsPanel } from "../CommsPanel";
import type { SimulationState } from "@/lib/types";

function setSecureContext(value: boolean) {
  Object.defineProperty(window, "isSecureContext", { value, configurable: true });
}

function setMediaDevices(value: unknown) {
  Object.defineProperty(navigator, "mediaDevices", { value, configurable: true });
}

function minimalState(): SimulationState {
  return {
    scenario: {
      frame_id: "demo-floor-1",
      casualties: [],
      responders: [
        {
          id: "responder-001",
          display_name: "Mock Responder Alpha",
          position: { frame_id: "demo-floor-1", x_m: 0, y_m: 0 },
          status: "available",
        },
      ],
    },
    assignments: [],
  } as unknown as SimulationState;
}

afterEach(() => {
  cleanup();
});

describe("CommsPanel voice link", () => {
  it("explains insecure contexts instead of surfacing a raw TypeError", async () => {
    setSecureContext(false);
    setMediaDevices(undefined);

    render(<CommsPanel state={minimalState()} proposal={null} fighterId="responder-001" onStatus={() => {}} placing={false} onPlace={() => {}} busy={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable voice link" }));

    const matches = await screen.findAllByText(/insecure connection/i);
    expect(matches.length).toBeGreaterThanOrEqual(2); // short status line + full explanation
    expect(screen.getByText(/serve it over https/i)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/cannot read properties/i);
  });
});
