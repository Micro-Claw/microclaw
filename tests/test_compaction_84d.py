from microclaw.conversation import AuditLog, ConversationStore, estimate_tokens


def test_local_valve_uses_only_calibrated_size_to_choose_to_fold():
    # Gate 84's measured server budget: history ceiling equals high-water in
    # raw estimate units when the probes answer, and ratio maps it to server
    # tokens. The final two turns keep the best fold above low-water so the
    # valve is the only reason it can commit.
    history = []
    for index in range(20):
        size = 45000 if index >= 18 else 5000
        history.extend([
            {"role": "user", "content": "q" * size},
            {"role": "assistant", "content": [{"type": "text", "text": "a" * size}]},
        ])
    n_ctx, fixed = 131072, 33864
    high = n_ctx - fixed - 8192 - 4096
    low = int(high * .75)
    assert (high, low) == (84920, 63690)
    raw_estimate = estimate_tokens(history)
    assert raw_estimate > high
    assert raw_estimate * .5 <= high
    assert raw_estimate * .6 > high
    store = ConversationStore(AuditLog(None, enabled=False),
                              high_water_tokens=high, low_water_tokens=low)
    store.local_context_ceiling = high
    store.local_fixed_tokens = fixed

    store.local_estimate_ratio = 0.5
    sent = store.model_messages(history)
    assert sent == history
    assert store.compaction_count == 0

    store.local_estimate_ratio = 0.6
    folded = store.model_messages(history)
    assert store.compaction_count == 1
    assert folded != history
    assert estimate_tokens(folded) > low
    assert estimate_tokens(folded) <= high


def test_gate_shape_commits_once_when_fold_reaches_low_water():
    def turn(index, size=100, huge_tool_result=False):
        if huge_tool_result:
            return [
                {"role": "user", "content": "q" * size},
                {"role": "assistant", "content": [{"type": "tool_use",
                    "id": f"call-{index}", "name": "read", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result",
                    "tool_use_id": f"call-{index}", "content": "x" * size}]},
                {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
            ]
        return [
            {"role": "user", "content": "q" * size},
            {"role": "assistant", "content": [{"type": "text", "text": "a" * size}]},
        ]

    local = ConversationStore(AuditLog(None, enabled=False),
                              high_water_tokens=1500, low_water_tokens=1125)
    local.local_context_ceiling = 100_000  # Safety valve stays out of this case.
    old_d9 = ConversationStore(AuditLog(None, enabled=False),
                               high_water_tokens=1500, low_water_tokens=1125)
    history = []
    local_commits, local_sizes, old_commits = [], [], []
    rounds = [(100, False), (100, False), (2000, True)] + [(100, False)] * 7
    for index, (size, huge) in enumerate(rounds):
        history.extend(turn(index, size, huge))
        local_before, old_before = local.compaction_count, old_d9.compaction_count
        local.model_messages(history)
        old_d9.model_messages(history)
        if local.compaction_count > local_before:
            local_commits.append(index)
            local_sizes.append(local.last_estimated_tokens)
        if old_d9.compaction_count > old_before:
            old_commits.append(index)
    assert local_commits == [4]
    assert all(size <= local.low_water_tokens for size in local_sizes)
    assert old_commits == [4, 9]
