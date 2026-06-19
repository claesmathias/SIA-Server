"""
Galaxy SIA Notification Handler

This module is responsible for formatting and sending notifications
via Apprise to any supported destination based on a parsed GalaxyEvent.
"""

import logging
import re
import sys
import time
from typing import Dict, Union
from queue import Queue, Full as QueueFull, Empty
from threading import Thread, Event as ThreadEvent
from galaxy.parser import GalaxyEvent


class MessageEvent:
    """
    A generic notification message not tied to a specific SIA alarm event.
    Used for watchdog heartbeat alerts and other system-level notifications.
    Priority is fixed by the caller rather than derived from event codes.
    """
    def __init__(self, account: str, site_name: str, message: str, priority: int):
        self.account           = account
        self.site_name         = site_name
        self.action_text       = message
        self.priority          = priority
        self.event_code        = None
        self.event_description = message
        self.time              = None

# --- Dependency and Logging Initialization ---

# Apply a basic config immediately so startup messages are always captured.
# This will be overridden by the main server's full logging setup later.
logging.basicConfig()
log = logging.getLogger(__name__)

# --- Force PyOpenSSL to be used by requests (if available) ---
try:
    import urllib3.contrib.pyopenssl
    urllib3.contrib.pyopenssl.inject_into_urllib3()
    log.info("Successfully injected PyOpenSSL into urllib3 for robust HTTPS.")
except ImportError:
    # On Windows, this may be a problem:
    if sys.platform == "win32":
        log.warning("PyOpenSSL not found. HTTPS notifications may fail on Windows without it.")
        log.warning("If you get HTTPS SSL problems, please run: python -m pip install pyopenssl")
    # On Linux, it's normal:
    else:
        log.info("PyOpenSSL not available; using default system SSL context.")

# --- CRITICAL: Check for 'apprise' library ---
try:
    import apprise
except ImportError:
    log.critical("="*60)
    log.critical("FATAL ERROR: The 'apprise' library is not installed.")
    log.critical("This library is required to send notifications.")
    if sys.platform == "win32":
        log.critical("Please install it by running: python -m pip install apprise")
    else: # Assume Linux/macOS
        log.critical("Please install it by running: python3 -m pip install apprise")
    log.critical("="*60)
    sys.exit(1) # Exit the entire application immediately.


def mask_url_credentials(url: str) -> str:
    """Masks tokens/passwords in Apprise URLs before they reach the logs."""
    # scheme://user:secret@host  or  scheme://token@host
    return re.sub(r'(://)([^/@]+)(@)', r'\1***\3', url)


def get_event_priority(event_code: str, priority_map: Dict, default_priority: int) -> int:
    """Gets the notification priority for a given event code from the defaults map."""
    return priority_map.get(event_code, default_priority)


def format_notification_text(event: Union[GalaxyEvent, MessageEvent]) -> str:
    """
    Formats the notification message text.
    For MessageEvent, returns action_text directly.
    For GalaxyEvent, chooses the rich ASCII block text (if available)
    or constructs a message from the Data block fields.
    """
    if isinstance(event, MessageEvent):
        return event.action_text

    event_time = event.time or "??"

    # If we have the rich text from the ASCII block, use it (SIA Level 3+)
    if event.action_text:
        notification = f"{event_time} {event.action_text}"
        # Digit-boundary match: zone "101" must not be considered "present"
        # inside text that merely contains "1010".
        if event.zone and not re.search(r'(?<!\d)' + re.escape(event.zone) + r'(?!\d)',
                                        str(event.action_text)):
            notification += f" (Zone {event.zone})"
    # Otherwise, build a basic message from the Data block fields (SIA Level 2)
    else:
        notification = f"{event_time}"
        if event.event_code:
            notification += f" Event: {event.event_code} ({event.event_description})"
        if event.subscriber_id:
            notification += f" User: {event.subscriber_id}"
        if event.zone:
            notification += f" Zone: {event.zone}"
        if event.area_id:
            notification += f" Area: {event.area_id}"
        if event.peripheral_id:
            notification += f" Peripheral: {event.peripheral_id}"
        if event.value:
            notification += f" Value: {event.value}"

    return notification.strip()


def _map_priority_to_notify_type(priority: int) -> 'apprise.NotifyType':
    """Maps Galaxy event priority to an Apprise notification type."""
    if priority <= 2:
        return apprise.NotifyType.INFO
    if priority == 3:
        return apprise.NotifyType.SUCCESS
    if priority == 4:
        return apprise.NotifyType.WARNING
    return apprise.NotifyType.FAILURE


def _dispatch_apprise_notification(event: Union[GalaxyEvent, MessageEvent], apprise_topics: Dict,
                                  priority_map: Dict, default_priority: int) -> bool:
    """Sends a formatted notification using topic-specific Apprise configuration."""

    topic_config = apprise_topics.get(event.account, apprise_topics.get('default'))

    if not topic_config or not topic_config.get('enabled', False):
        log.debug("Notifications disabled for account '%s' or default topic. Skipping.", event.account)
        return False

    apprise_urls = topic_config.get('urls', [])
    if not apprise_urls:
        log.warning("No valid Apprise services found for account '%s' or default. Skipping.", event.account)
        return False

    message = format_notification_text(event)
    if isinstance(event, MessageEvent):
        priority = event.priority
    else:
        priority = get_event_priority(event.event_code, priority_map, default_priority)

    notification_title = topic_config.get('title', 'Galaxy Alarm')
    account_display = event.site_name or event.account
    title = f"{notification_title}: {account_display}"
    notify_type = _map_priority_to_notify_type(priority)

    apprise_client = topic_config.get('apprise')
    if apprise_client is None:
        apprise_client = apprise.Apprise()
        added_any = False
        for u in apprise_urls:
            if not u or 'your-url' in u.lower() or 'your-url-here' in u.lower():
                log.warning("Skipping invalid Apprise URL for account %s: %s", event.account, u)
                continue
            if apprise_client.add(u):
                added_any = True
            else:
                log.error("Failed to initialize Apprise URL for account %s: %s", event.account, u)

        if not added_any:
            log.error("No valid Apprise services available for account %s; skipping.", event.account)
            return False

        topic_config['apprise'] = apprise_client

    log.debug("Sending notification (priority %d) to %s: %s", priority,
              ','.join(mask_url_credentials(u) for u in apprise_urls), message)
    log.info("Sending notification (priority %d) for account %s: %s", priority, account_display, message)

    try:
        success = apprise_client.notify(
            body=message,
            title=title,
            notify_type=notify_type
        )
        if success:
            log.debug("Dispatch successful for account %s.", event.account)
            return True
        log.error("Dispatch failed for account %s: Apprise returned False.", event.account)
        return False

    except Exception as e:
        log.error("Dispatch failed for account %s: %s", event.account, e)
        return False

class NotificationDispatcher(Thread):
    """
    A non-blocking background thread that processes a queue of notifications.
    It handles sending and retries with progressive backoff without blocking the queue.
    """
    def __init__(self, queue: Queue, apprise_topics: Dict, priority_map: Dict,
                 default_priority: int, max_retries: int, max_retry_time: int):
        super().__init__(daemon=True)
        self.name = "NotificationDispatcher"
        self.queue = queue
        self.apprise_topics = apprise_topics
        self.priority_map = priority_map
        self.default_priority = default_priority
        self.max_retries = max_retries
        self.max_retry_time_minutes = max_retry_time
        self.shutdown_event = ThreadEvent()

    def get_retry_delay(self, retry_count: int) -> int:
        """
        Calculates the retry delay using a progressive backoff strategy (exponential backoff).
        The delay doubles with each retry, up to the configured maximum.
        """
        # Start with a 1-minute base delay
        base_delay = 1 # in minutes

        # Double the delay for each previous attempt (2^0, 2^1, 2^2, ...)
        # The 'retry_count' starts at 1 for the first retry.
        current_delay = base_delay * (2 ** (retry_count - 1))

        # Ensure the delay does not exceed the user-configured maximum
        final_delay = min(current_delay, self.max_retry_time_minutes)
        
        return final_delay * 60 # Convert minutes to seconds

    def run(self):
        log.info("NotificationDispatcher thread started.")
        pending_retries = []  # items not yet due: list of (event, retry_count, next_attempt_time)
        while not self.shutdown_event.is_set():
            # Re-inject any retries that have become due. Keeping them in a
            # local list (instead of cycling them through the queue) avoids
            # the previous 1-second busy loop and never delays fresh events.
            now = time.time()
            due = [item for item in pending_retries if item[2] <= now]
            for item in due:
                pending_retries.remove(item)
                try:
                    self.queue.put_nowait(item)
                except QueueFull:
                    log.error("Queue full. Dropping due retry for account %s.",
                              getattr(item[0], 'account', '?'))

            try:
                event, retry_count, next_attempt_time = self.queue.get(timeout=1.0)
            except Empty:
                continue
            if not event:  # This is the shutdown signal
                self.queue.task_done()
                break

            if time.time() < next_attempt_time:
                # Not due yet - park it locally and move on.
                pending_retries.append((event, retry_count, next_attempt_time))
                self.queue.task_done()
                continue

            success = _dispatch_apprise_notification(event, self.apprise_topics, self.priority_map, self.default_priority)

            if not success:
                # The notification failed. Schedule it for a future retry.
                retry_count += 1
                if self.max_retries == 0 or retry_count <= self.max_retries:
                    delay = self.get_retry_delay(retry_count)
                    new_next_attempt_time = time.time() + delay
                    log.warning("Dispatch failed for account %s. Re-queueing for retry in %d mins (attempt %d).",
                                event.account, delay // 60, retry_count)

                    pending_retries.append((event, retry_count, new_next_attempt_time))
                else:
                    log.error("Dispatch failed for account %s after %d retries. Giving up.",
                              event.account, self.max_retries)

            self.queue.task_done()
        log.info("NotificationDispatcher thread stopped.")

    def stop(self):
        log.info("Stopping NotificationDispatcher thread...")
        self.shutdown_event.set()
        self.queue.put((None, 0, 0)) # Unblock the .get() call


def _enqueue(event: Union[GalaxyEvent, MessageEvent], queue: Queue):
    """
    Puts a new event onto the notification queue.

    If the queue is full, the oldest item is evicted to make space. The
    eviction loop retries a couple of times because the dispatcher thread
    may consume/produce concurrently (avoids a check-then-act race).
    """
    for _ in range(3):
        try:
            queue.put_nowait((event, 0, 0))  # event, retry_count, next_attempt_time
            log.debug("Event for account %s added to notification queue.", event.account)
            return
        except QueueFull:
            try:
                dropped, _, _ = queue.get_nowait()
                queue.task_done()
                log.warning("Notification queue full. Dropped oldest event (account %s) "
                            "to make room for new event (account %s).",
                            getattr(dropped, 'account', '?'), event.account)
            except Empty:
                pass
    log.error("Notification queue is still full! Event for %s was lost.", event.account)


def enqueue_notification(event: GalaxyEvent, queue: Queue):
    """Puts a SIA GalaxyEvent onto the notification queue."""
    _enqueue(event, queue)


def enqueue_message_notification(account: str, site_name: str,
                                  message: str, priority: int,
                                  queue: Queue):
    """
    Puts a generic message notification onto the notification queue.
    Used by ip_check.py for watchdog heartbeat-lost/restored alerts.
    """
    _enqueue(MessageEvent(account, site_name, message, priority), queue)
