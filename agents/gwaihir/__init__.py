"""Gwaihir: Telegram transport.

Inbound: long-poll ``getUpdates``, allowlist-gate by chat ID, forward
each message to the API at ``/execute/wait``, post the reply back via
``sendMessage``.

Outbound: :func:`agents.gwaihir.notifier.send_message` — used by
Galadriel's ``announce`` hook to deliver scheduled reminders.
"""
