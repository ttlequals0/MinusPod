"""Per-run context keyed by thread: two runs accumulate separately."""
import threading
from concurrent.futures import ThreadPoolExecutor

import run_context


def test_begin_binds_current_thread():
    ctx = run_context.begin('feed', 'ep1')
    try:
        assert run_context.current() is ctx
        assert ctx.key == 'feed:ep1'
    finally:
        run_context.end(ctx)
    assert run_context.current() is None


def test_two_threads_have_separate_contexts():
    seen = {}

    def run(name):
        ctx = run_context.begin('feed', name)
        try:
            ctx.tokens.start()
            ctx.tokens.add(1, 2, 0.5)
            seen[name] = run_context.current().tokens.collect_and_reset()
        finally:
            run_context.end(ctx)

    a = threading.Thread(target=run, args=('a',))
    b = threading.Thread(target=run, args=('b',))
    a.start(); b.start(); a.join(); b.join()
    assert seen['a'] == {'input_tokens': 1, 'output_tokens': 2, 'cost': 0.5}
    assert seen['b'] == {'input_tokens': 1, 'output_tokens': 2, 'cost': 0.5}


def test_worker_thread_binds_to_submitters_run():
    ctx = run_context.begin('feed', 'ep1')
    try:
        ctx.tokens.start()
        with ThreadPoolExecutor(max_workers=2) as exe:
            fut = exe.submit(run_context.run_in_worker_thread(lambda: run_context.current()))
            assert fut.result() is ctx
            fut2 = exe.submit(run_context.run_in_worker_thread(
                lambda: run_context.current().tokens.add(3, 0, 0.0)))
            fut2.result()
        assert ctx.tokens.collect_and_reset()['input_tokens'] == 3
    finally:
        run_context.end(ctx)


def test_worker_without_run_sees_none():
    with ThreadPoolExecutor(max_workers=1) as exe:
        assert exe.submit(run_context.run_in_worker_thread(lambda: run_context.current())).result() is None


def test_tokens_inactive_before_start():
    ctx = run_context.begin('feed', 'ep1')
    try:
        assert ctx.tokens.is_active() is False
        ctx.tokens.start()
        assert ctx.tokens.is_active() is True
    finally:
        run_context.end(ctx)


def test_thinking_notices_are_run_fenced_and_deduplicated_across_workers():
    ctx = run_context.begin('feed', 'ep1', run_id='run-a')
    notice = {
        'pass': 'ad_detection_pass_1',
        'provider': 'openai-compatible',
        'model': 'test-model',
        'requested': 'none',
        'compatibility': 'required',
        'fallback': {
            'max_tokens': 4096,
            'temperature': 0.0,
            'reasoning_effort': None,
        },
    }
    try:
        with ThreadPoolExecutor(max_workers=4) as exe:
            futures = [exe.submit(run_context.run_in_worker_thread(
                lambda: run_context.current().add_thinking_notice(
                    'run-a', notice))) for _ in range(8)]
            assert all(future.result() for future in futures)

        assert ctx.thinking_notices('run-a') == [notice]
        assert ctx.thinking_notices('run-b') == []
        assert ctx.add_thinking_notice('run-b', notice) is False
    finally:
        run_context.end(ctx)


def test_thinking_notice_merge_prefers_specific_compatibility():
    ctx = run_context.RunContext('feed', 'ep1', run_id='run-a')
    notice = {
        'pass': 'ad_detection_pass_1',
        'provider': 'openai-compatible',
        'model': 'test-model',
        'requested': 'none',
        'compatibility': 'incompatible',
        'fallback': {
            'max_tokens': 4096,
            'temperature': 0.0,
            'reasoning_effort': None,
        },
    }

    assert ctx.add_thinking_notice('run-a', notice) is True
    assert ctx.add_thinking_notice(
        'run-a', {**notice, 'compatibility': 'required'}) is True
    assert ctx.thinking_notices('run-a')[0]['compatibility'] == 'required'

    assert ctx.add_thinking_notice(
        'run-a', {**notice, 'compatibility': 'unsupported'}) is True
    assert ctx.thinking_notices('run-a')[0]['compatibility'] == 'incompatible'
