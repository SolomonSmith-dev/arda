"""Prometheus counters for Tom Bombadil.

prometheus_client is optional -- if it isn't installed (slim install,
test environment), every helper turns into a no-op so the rest of the
package keeps working. Callers don't need to feature-check.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter
    _PROM_AVAILABLE = True
except ImportError:  # pragma: no cover -- slim install path
    Counter = None  # type: ignore[misc,assignment]
    _PROM_AVAILABLE = False


def _counter(name: str, doc: str, labelnames: tuple[str, ...] = ()) -> Any:
    if not _PROM_AVAILABLE:
        return _NoopCounter()
    return Counter(name, doc, labelnames=labelnames)


class _NoopCounter:
    def labels(self, *_args: Any, **_kwargs: Any) -> _NoopCounter:
        return self

    def inc(self, _amount: float = 1.0) -> None:
        return


REPLIES = _counter("tom_replies_total", "Tom Bombadil reply events", ("tier",))
PREF_SUPPRESSED = _counter(
    "tom_pref_suppressed_total",
    "Times suppress_films pref swapped the film system block",
)
FACTS_INGESTED = _counter(
    "tom_facts_ingested_total",
    "Free-form facts embedded into Finrod long-term memory",
)
DRAFTS_OFFERED = _counter(
    "tom_drafts_offered_total",
    "NoteDraft confirmation prompts posted to Discord",
)
DRAFTS_COMMITTED = _counter(
    "tom_drafts_committed_total",
    "NoteDrafts committed via the ✅ reaction path",
)
DRAFTS_SKIPPED = _counter(
    "tom_drafts_skipped_total",
    "NoteDrafts skipped via the ❌ reaction path",
)
SLASH_COMMANDS = _counter(
    "tom_slash_commands_total",
    "Slash command invocations by name",
    ("name",),
)
GUARDS_TRIPPED = _counter(
    "tom_guards_tripped_total",
    "Abuse-guard rejections by kind (rate_limit | ban | prompt_too_long)",
    ("kind",),
)
LETTERBOXD_SYNC = _counter(
    "tom_letterboxd_sync_total",
    "Letterboxd sync runs by outcome (saved_films | errors)",
    ("kind",),
)
