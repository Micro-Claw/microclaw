/*
  Browser confirmation recovery state, kept independent of the DOM so the
  reconciliation contract can be exercised directly under node.
*/
(function (global) {
  "use strict";

  async function* sseEvents(body, onFrame = () => {}) {
    const reader = body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return;
      buf += value;
      let frameEnd;
      while ((frameEnd = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, frameEnd);
        buf = buf.slice(frameEnd + 2);
        onFrame();
        for (const line of frame.split("\n")) {
          if (line.startsWith("data:")) yield JSON.parse(line.slice(5));
        }
      }
    }
  }

  function create(options) {
    const fetchState = options.fetch;
    const now = options.now;
    const setTimer = options.setTimeout;
    const clearTimer = options.clearTimeout;
    const isVisible = options.isVisible;
    const getConfirmId = options.getConfirmId;
    const getGrantIds = options.getGrantIds;
    const showGrants = options.showGrants;
    const showConfirm = options.showConfirm;
    const hideConfirm = options.hideConfirm;
    const showRemaining = options.showRemaining;
    const notePollFailure = options.notePollFailure;
    const needsPairing = options.needsPairing;
    const noteStreamSilence = options.noteStreamSilence || (() => {});
    const settleRecovered = options.settleRecovered || (() => {});
    const silenceMs = options.silenceMs == null ? 30000 : options.silenceMs;
    let recoveryActive = false;
    let pollInFlight = false;
    let pollTimer = null;
    let pollFailures = 0;
    let currentTurnId = null;
    let displayedResolutionId = null;
    let deadlineMs = null;
    let silenceTimer = null;
    let streamSilenceDetected = false;

    function armSilenceTimer() {
      clearSilenceTimer();
      silenceTimer = setTimer(markStreamSilent, silenceMs);
    }

    function clearSilenceTimer() {
      if (silenceTimer !== null) clearTimer(silenceTimer);
      silenceTimer = null;
    }

    function markStreamSilent() {
      silenceTimer = null;
      if (streamSilenceDetected) return;
      streamSilenceDetected = true;
      noteStreamSilence();
    }

    function settleIfRecovered(state) {
      if (!streamSilenceDetected || !state || state.running !== false) return false;
      streamSilenceDetected = false;
      clearSilenceTimer();
      stopConfirmationRecovery();
      settleRecovered();
      return true;
    }

    function reconcileGrants(grants) {
      const incoming = (grants || []).map((grant) => grant.id);
      const rendered = getGrantIds();
      if (incoming.length === rendered.length &&
          incoming.every((id, index) => id === rendered[index])) return false;
      showGrants(grants);
      return true;
    }

    function setTurnId(turnId) {
      currentTurnId = turnId;
      displayedResolutionId = null;
    }

    function updateRemaining(remainingSeconds) {
      if (remainingSeconds == null) {
        deadlineMs = null;
        return;
      }
      deadlineMs = now() + Math.max(0, remainingSeconds) * 1000;
      showRemaining(Math.max(0, Math.ceil((deadlineMs - now()) / 1000)));
    }

    async function reconcileConfirmation() {
      const response = await fetchState("/api/confirm");
      if (response.status === 401) {
        stopConfirmationRecovery();
        needsPairing();
        return null;
      }
      if (!response.ok) throw new Error(`Confirmation recovery failed (${response.status})`);
      const pending = await response.json();
      pollFailures = 0;
      reconcileGrants(pending.grants);
      if (!currentTurnId && pending.turn_id) currentTurnId = pending.turn_id;
      const confirmId = getConfirmId();
      if (pending.id) {
        if (confirmId !== pending.id) showConfirm(pending);
        updateRemaining(pending.remaining_s);
      } else {
        updateRemaining(null);
        const resolution = pending.last_resolution;
        const matches = resolution && currentTurnId &&
          resolution.turn_id === currentTurnId &&
          (!confirmId || resolution.id === confirmId);
        if (matches && displayedResolutionId !== resolution.id) {
          displayedResolutionId = resolution.id;
          hideConfirm(resolution.id, resolution.decision);
        } else if (confirmId) {
          hideConfirm(confirmId);
        }
      }
      settleIfRecovered(pending);
      if (pending.running === false) stopConfirmationRecovery();
      return pending;
    }

    function stopConfirmationRecovery() {
      recoveryActive = false;
      if (pollTimer !== null) clearTimer(pollTimer);
      pollTimer = null;
    }

    function nextDelay() {
      return pollFailures < 3 ? 1000 : Math.min(30000, 1000 * (2 ** (pollFailures - 2)));
    }

    async function pollConfirmation() {
      if (pollTimer !== null) clearTimer(pollTimer);
      pollTimer = null;
      if (!recoveryActive || pollInFlight) return;
      if (!isVisible()) {
        pollTimer = setTimer(pollConfirmation, 1000);
        return;
      }
      pollInFlight = true;
      try {
        await reconcileConfirmation();
      } catch (error) {
        pollFailures += 1;
        notePollFailure(pollFailures, error);
      } finally {
        pollInFlight = false;
        if (recoveryActive) pollTimer = setTimer(pollConfirmation, nextDelay());
      }
    }

    function startConfirmationRecovery() {
      if (recoveryActive) return;
      recoveryActive = true;
      void pollConfirmation();
    }

    function startIfRunning(state) {
      if (state && state.running === true) startConfirmationRecovery();
    }

    async function startFromBoot() {
      const state = await reconcileConfirmation();
      startIfRunning(state);
      return state;
    }

    function resumeConfirmationRecovery() {
      if (recoveryActive && isVisible()) void pollConfirmation();
    }

    return {
      reconcileConfirmation, reconcileGrants, startConfirmationRecovery,
      startIfRunning, startFromBoot, stopConfirmationRecovery, pollConfirmation,
      resumeConfirmationRecovery,
      setTurnId, armSilenceTimer, clearSilenceTimer, markStreamSilent,
      settleIfRecovered, now,
    };
  }

  global.Recovery = { create, sseEvents };
})(typeof window !== "undefined" ? window : globalThis);
