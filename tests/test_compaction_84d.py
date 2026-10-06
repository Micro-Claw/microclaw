from microclaw.conversation import AuditLog, ConversationStore, estimate_tokens


def _history(turns=20, size=500):
    history = []
    for index in range(turns):
        history.extend([
            {"role": "user", "content": "q" * size},
            {"role": "assistant", "content": [{"type": "text", "text": "a" * size}]},
        ])
    return history


def test_local_valve_uses_only_calibrated_size_to_choose_to_fold():
    history = _history()
    store = ConversationStore(AuditLog(None, enabled=False),
                              high_water_tokens=5000, low_water_tokens=3000)
    store.local_context_ceiling = 5000
    store.local_fixed_tokens = 100

    store.local_estimate_ratio = 0.2
    sent = store.model_messages(history)
    assert sent == history
    assert store.compaction_count == 0

    store.local_estimate_ratio = 2.0
    folded = store.model_messages(history)
    assert store.compaction_count == 1
    assert folded != history
    assert estimate_tokens(folded) <= store.high_water_tokens


def test_local_hysteresis_waits_for_low_water_when_server_view_fits():
    history = _history(turns=10, size=100)
    store = ConversationStore(AuditLog(None, enabled=False),
                              high_water_tokens=1000, low_water_tokens=700)
    store.local_context_ceiling = 100_000
    store.local_estimate_ratio = 0.5
    assert store.model_messages(history) == history
    assert store.compaction_count == 0
