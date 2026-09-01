/*
  Browser confirmation recovery state, kept independent of the DOM so the
  reconciliation contract can be exercised directly under node.
*/
(function (global) {
  "use strict";

  function create(options) {
    const fetchState = options.fetch;
    const now = options.now;
    const getConfirmId = options.getConfirmId;
    const getGrantIds = options.getGrantIds;
    const showGrants = options.showGrants;
    const showConfirm = options.showConfirm;
    const hideConfirm = options.hideConfirm;

    function reconcileGrants(grants) {
      const incoming = (grants || []).map((grant) => grant.id);
      const rendered = getGrantIds();
      if (incoming.length === rendered.length &&
          incoming.every((id, index) => id === rendered[index])) return false;
      showGrants(grants);
      return true;
    }

    async function reconcileConfirmation() {
      const response = await fetchState("/api/confirm");
      const pending = await response.json();
      reconcileGrants(pending.grants);
      const confirmId = getConfirmId();
      if (pending.id) {
        if (confirmId !== pending.id) showConfirm(pending);
      } else if (confirmId) {
        hideConfirm(confirmId);
      }
      return pending;
    }

    return { reconcileConfirmation, reconcileGrants, now };
  }

  global.Recovery = { create };
})(typeof window !== "undefined" ? window : globalThis);
