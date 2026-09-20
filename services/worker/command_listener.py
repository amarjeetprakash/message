"""
Standalone Telethon listener service for User Accounts.

In the old architecture, `worker/sender.py` kept Telegram channels open 24/7
and listened for commands (e.g. `.status`, `.addgroup`).
In the new architecture, ARQ workers are ephemeral and only connect when 
there is a job to process.

This service brings back the "always-on" listener for the users' connected
accounts so they can continue to send dot-commands in their Saved Messages.

Usage:
  python -m services.worker.command_listener
"""

import asyncio
import logging
import signal

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from core.logger import setup_service_logging
from core.database import init_database
from models.session import get_all_connected_sessions, mark_session_disabled
from shared.utils import get_telegram_client_kwargs
from worker.commands import process_command
from config import OWNER_ID

logger = logging.getLogger(__name__)


class CommandListenerService:
    """Manages long-lived Telethon clients purely for listening to dot-commands."""

    def __init__(self):
        self.running = False
        self.clients: dict[tuple, TelegramClient] = {}
        self._shutdown_event = asyncio.Event()

    async def start(self):
        setup_service_logging("listener")
        logger.info("=" * 50)
        logger.info("Command Listener Service Starting")
        logger.info("=" * 50)

        self.running = True
        await init_database()

        # Signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: self.stop())
            except NotImplementedError:
                pass

        # Main loop: periodically check for new sessions to listen to
        while self.running:
            try:
                await self._sync_clients()
                
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=60)
                    break
                except asyncio.TimeoutError:
                    continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Listener loop error: {e}")
                await asyncio.sleep(10)

        await self._stop_all_clients()

    async def _sync_clients(self):
        """Sync active DB sessions with running Telethon clients."""
        sessions = await get_all_connected_sessions()
        active_keys = {(s["user_id"], s["phone"]) for s in sessions if not s.get("worker_disabled")}

        # Start new clients
        for session in sessions:
            if session.get("worker_disabled"):
                continue
                
            key = (session["user_id"], session["phone"])
            if key not in self.clients:
                await self._start_client(session)

        # Stop removed clients
        for key in list(self.clients.keys()):
            if key not in active_keys:
                await self._stop_client(key)

    async def _start_client(self, session: dict):
        """Start listening for commands on a specific account."""
        user_id = session["user_id"]
        phone = session["phone"]
        session_string = session.get("session_string", "")
        api_id = session.get("api_id")
        api_hash = session.get("api_hash")

        if not session_string or not api_id or not api_hash:
            return

        client = TelegramClient(
            StringSession(session_string),
            api_id,
            api_hash,
            device_model="Command Listener",
            system_version="1.0",
            app_version="1.0",
            **get_telegram_client_kwargs()
        )

        client.phone = phone

        try:
            await client.connect()
            if not await client.is_user_authorized():
                logger.warning(f"Session unauthorized for listener: {phone}")
                await client.disconnect()
                await mark_session_disabled(user_id, phone, "listener_auth_failed")
                return

            self._register_handlers(client, user_id)
            self.clients[(user_id, phone)] = client
            logger.info(f"🎧 Started listening for commands on {phone}")

        except Exception as e:
            logger.error(f"Failed to start listener for {phone}: {e}")
            if client:
                await client.disconnect()

    def _register_handlers(self, client: TelegramClient, user_id: int):
        """Register the exact same event handlers the old worker used."""
        
        @client.on(events.NewMessage(outgoing=True))
        async def outgoing_handler(event):
            try:
                if not event.message or not event.message.text:
                    return
                text = event.message.text.strip()
                if text.startswith("."):
                    logger.info(f"Command detected: {text.split()[0]}")
                    await process_command(client, user_id, event.message)
            except Exception as e:
                logger.error(f"Outgoing command error: {e}")

        @client.on(events.NewMessage(incoming=True))
        async def incoming_handler(event):
            try:
                if not event.message or not event.message.text:
                    return
                text = event.message.text.strip()
                sender_id = event.sender_id
                
                # 1. Private DM commands from user or owner (targets this account ID)
                if text.startswith(".") and event.is_private and (sender_id == user_id or sender_id == OWNER_ID):
                    logger.info(f"Private command detected from {sender_id}: {text.split()[0]}")
                    await process_command(client, user_id, event.message)
                # 2. Shared Group/Channel commands from owner (broadcasts to all account IDs in the group)
                elif text.startswith(".") and (event.is_group or event.is_channel) and sender_id == OWNER_ID:
                    logger.info(f"Group broadcast command detected from Owner: {text.split()[0]}")
                    await process_command(client, user_id, event.message)
            except Exception as e:
                logger.error(f"Incoming command error: {e}")

    async def _stop_client(self, key: tuple):
        """Stop listening on a specific account."""
        client = self.clients.pop(key, None)
        if client:
            try:
                await client.disconnect()
                logger.info(f"Stopped listening on {key[1]}")
            except Exception:
                pass

    async def _stop_all_clients(self):
        """Disconnect all clients on shutdown."""
        logger.info(f"Stopping {len(self.clients)} listeners...")
        for key in list(self.clients.keys()):
            await self._stop_client(key)

    def stop(self):
        """Signal the service to stop."""
        if not self.running:
            return
        logger.info("Shutdown signal received...")
        self.running = False
        self._shutdown_event.set()


if __name__ == "__main__":
    listener = CommandListenerService()
    try:
        asyncio.run(listener.start())
    except KeyboardInterrupt:
        pass
