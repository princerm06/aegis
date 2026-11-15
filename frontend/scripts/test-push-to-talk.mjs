import test from "node:test";
import assert from "node:assert/strict";

test("PushToTalk state machine and callback deduplication", async (t) => {
  await t.test("deduplicates multiple rapid set(true) activations", () => {
    let talkingState = false;
    let trackEnabledCount = 0;
    const talkingRef = { current: false };
    const logs = [];

    const mockStream = {
      getAudioTracks: () => [
        {
          set enabled(val) {
            if (val) trackEnabledCount++;
          },
        },
      ],
    };

    const set = (on) => {
      const s = mockStream;
      if (!s) return;
      if (on) {
        if (talkingRef.current) return;
        talkingRef.current = true;
        s.getAudioTracks().forEach((t) => (t.enabled = true));
        talkingState = true;
      } else {
        if (!talkingRef.current) return;
        talkingRef.current = false;
        s.getAudioTracks().forEach((t) => (t.enabled = false));
        talkingState = false;
        logs.push("logged");
      }
    };

    // Simulate rapid repeated pointer/key down
    set(true);
    set(true);
    set(true);

    assert.equal(talkingState, true);
    assert.equal(talkingRef.current, true);
    assert.equal(trackEnabledCount, 1, "Tracks enabled exactly once");
    assert.equal(logs.length, 0, "No log during speech");

    // Simulate rapid repeated release
    set(false);
    set(false);
    set(false);

    assert.equal(talkingState, false);
    assert.equal(talkingRef.current, false);
    assert.equal(logs.length, 1, "Log emitted exactly once, no duplicates");
  });

  await t.test("keyboard filtering ignores typing in text fields", () => {
    const isTyping = (tagName) =>
      tagName === "INPUT" || tagName === "TEXTAREA" || tagName === "SELECT";

    assert.equal(isTyping("INPUT"), true);
    assert.equal(isTyping("TEXTAREA"), true);
    assert.equal(isTyping("SELECT"), true);
    assert.equal(isTyping("BUTTON"), false);
    assert.equal(isTyping("DIV"), false);
  });
});
