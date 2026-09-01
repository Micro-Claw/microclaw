/*
  Browser confirmation recovery state, kept independent of the DOM so the
  reconciliation contract can be exercised directly under node.
*/
(function (global) {
  "use strict";

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
    let recoveryActive = false;
    let pollInFlight = false;
    let pollTimer = null;
    let pollFailures = 0;
    let currentTurnId = null;
    let displayedResolutionId = null;
    let deadlineMs = null;

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

    function resumeConfirmationRecovery() {
      if (recoveryActive && isVisible()) void pollConfirmation();
    }

    return {
      reconcileConfirmation, reconcileGrants, startConfirmationRecovery,
      stopConfirmationRecovery, pollConfirmation, resumeConfirmationRecovery,
      setTurnId, now,
    };
  }

  global.Recovery = { create };
})(typeof window !== "undefined" ? window : globalThis);
