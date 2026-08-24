#!/bin/sh
set -eu

usage() {
    echo "usage: $0 {start|revise} WORKTREE JOB_DIR PROMPT_FILE" >&2
    exit 2
}

[ "$#" -eq 4 ] || usage
mode=$1
worktree=$2
job_dir=$3
prompt_file=$4

case "$mode" in
    start|revise) ;;
    *) usage ;;
esac

command -v codex >/dev/null 2>&1 || {
    echo "codex is not installed or not on PATH" >&2
    exit 127
}
[ -d "$worktree" ] || {
    echo "worktree does not exist: $worktree" >&2
    exit 2
}
[ -f "$prompt_file" ] || {
    echo "prompt file does not exist: $prompt_file" >&2
    exit 2
}

worktree=$(cd "$worktree" && pwd -P)
prompt_dir=$(cd "$(dirname "$prompt_file")" && pwd -P)
prompt_file=$prompt_dir/$(basename "$prompt_file")
mkdir -p "$job_dir"
job_dir=$(cd "$job_dir" && pwd -P)

git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "not a git worktree: $worktree" >&2
    exit 2
}
[ -f "$worktree/.git" ] || {
    echo "refusing to run in a primary checkout; use a linked implementation worktree: $worktree" >&2
    exit 2
}

session_file=$job_dir/session-id
result_file=$job_dir/result.md
status_file=$job_dir/status

if [ "$mode" = start ]; then
    [ ! -e "$session_file" ] || {
        echo "job already has a session; use revise or a new job directory" >&2
        exit 2
    }
    stem=initial
else
    [ -s "$session_file" ] || {
        echo "cannot revise without a non-empty session-id: $session_file" >&2
        exit 2
    }
    revision_count=$(find "$job_dir" -maxdepth 1 -name 'revision-*.events.jsonl' -print | wc -l | tr -d ' ')
    revision_count=$((revision_count + 1))
    stem=$(printf 'revision-%03d' "$revision_count")
fi

events_file=$job_dir/$stem.events.jsonl
stderr_file=$job_dir/$stem.stderr.log
tmp_result=$job_dir/.$stem.result.tmp
tmp_status=$job_dir/.status.tmp
rm -f "$tmp_result" "$tmp_status"

set +e
if [ "$mode" = start ]; then
    codex exec \
        --cd "$worktree" \
        --approve-for-me \
        --json \
        --output-last-message "$tmp_result" \
        - < "$prompt_file" > "$events_file" 2> "$stderr_file"
else
    session_id=$(tr -d '\r\n' < "$session_file")
    (
        cd "$worktree"
        codex exec resume \
            --strict-config \
            -c 'approval_policy="on-request"' \
            -c 'approvals_reviewer="auto_review"' \
            "$session_id" \
            --json \
            --output-last-message "$tmp_result" \
            - < "$prompt_file"
    ) > "$events_file" 2> "$stderr_file"
fi
run_status=$?
set -e

printf '%s\n' "$run_status" > "$tmp_status"
mv "$tmp_status" "$status_file"

if [ -s "$tmp_result" ]; then
    mv "$tmp_result" "$result_file"
else
    rm -f "$tmp_result"
fi

if [ "$mode" = start ]; then
    session_id=$(sed -nE 's/.*"thread_id"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$events_file" | head -n 1)
    if [ -n "$session_id" ]; then
        printf '%s\n' "$session_id" > "$session_file"
    fi
fi

if [ "$run_status" -ne 0 ]; then
    echo "Codex exited with status $run_status; see $stderr_file and $events_file" >&2
    exit "$run_status"
fi
[ -s "$result_file" ] || {
    printf '1\n' > "$status_file"
    echo "Codex succeeded without writing a final result: $result_file" >&2
    exit 1
}
[ -s "$session_file" ] || {
    printf '1\n' > "$status_file"
    echo "Codex succeeded but no session id was found in $events_file" >&2
    exit 1
}

echo "Codex $mode completed: $result_file"
