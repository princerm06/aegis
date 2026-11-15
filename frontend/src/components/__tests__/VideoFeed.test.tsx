import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { VideoFeed } from "../VideoFeed";

function setSecureContext(value: boolean) {
  Object.defineProperty(window, "isSecureContext", { value, configurable: true });
}

function setMediaDevices(value: unknown) {
  Object.defineProperty(navigator, "mediaDevices", { value, configurable: true });
}

function fakeStream() {
  return { getTracks: () => [{ stop: () => {} }] } as unknown as MediaStream;
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("VideoFeed camera setup", () => {
  it("explains insecure contexts instead of throwing on Connect", async () => {
    setSecureContext(false);
    setMediaDevices(undefined);

    render(<VideoFeed feedId="insecure-connect" title="TEST CAM" />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    expect(await screen.findByText(/local camera unavailable on this connection/i)).toBeTruthy();
    // Scope: the viewing browser's camera, not the remote CV publisher.
    expect(screen.getByText(/viewing device/i)).toBeTruthy();
    // Manual stream URLs stay available.
    expect(screen.getByPlaceholderText(/stream url/i)).toBeTruthy();
    // No raw TypeError anywhere.
    expect(document.body.textContent).not.toMatch(/cannot read properties/i);
  });

  it("shows a friendly error — not a crash — for a saved device source on an insecure origin", async () => {
    setSecureContext(false);
    setMediaDevices(undefined);
    window.localStorage.setItem("aegis.feed.saved-insecure", JSON.stringify({ kind: "device", deviceId: "cam-1" }));

    render(<VideoFeed feedId="saved-insecure" title="TEST CAM" />);

    expect(await screen.findByText(/insecure connection/i)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/cannot read properties/i);
  });

  it("lists cameras and opens the chosen device on supported origins", async () => {
    setSecureContext(true);
    const getUserMedia = vi.fn(async () => fakeStream());
    const enumerateDevices = vi.fn(async () => [
      { deviceId: "cam-1", kind: "videoinput", label: "Fake Cam", groupId: "" },
    ]);
    setMediaDevices({ getUserMedia, enumerateDevices });

    render(<VideoFeed feedId="supported-flow" title="TEST CAM" />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    expect(await screen.findByText(/fake cam/i)).toBeTruthy();
    fireEvent.click(screen.getByText(/fake cam/i));

    await vi.waitFor(() => {
      expect(getUserMedia).toHaveBeenCalledWith(
        expect.objectContaining({ video: expect.objectContaining({ deviceId: { exact: "cam-1" } }) }),
      );
    });
    expect(document.body.textContent).not.toMatch(/cannot read properties/i);
  });

  it("maps a denied permission to guidance on supported origins", async () => {
    setSecureContext(true);
    setMediaDevices({
      getUserMedia: async () => {
        throw new DOMException("Permission denied", "NotAllowedError");
      },
      enumerateDevices: async () => [],
    });

    render(<VideoFeed feedId="denied-flow" title="TEST CAM" />);
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    expect(await screen.findByText(/permission denied/i)).toBeTruthy();
  });
});
