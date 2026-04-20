"""Discord notifier — posts trade updates via openclaw message CLI.

Called after each trading cycle. Reads notification file and sends to Discord.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

NOTIFICATION_FILE = Path(__file__).parent.parent / "trades" / ".last_notification"
CHANNEL_ID = "1495630671635415111"


def send_discord(message: str) -> bool:
    """Send message to Discord via openclaw."""
    try:
        # Escape special chars for shell
        safe_msg = message.replace("'", "'\\''").replace('"', '\\"')
        result = subprocess.run(
            [
                "openclaw", "message", "send",
                "--channel", "discord",
                "--target", CHANNEL_ID,
                "--message", safe_msg,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Discord send failed: {e}")
        return False


def main() -> None:
    if not NOTIFICATION_FILE.exists():
        print("No notification to send")
        sys.exit(0)

    message = NOTIFICATION_FILE.read_text().strip()
    if not message:
        sys.exit(0)

    # Skip heartbeat/empty messages
    if "HEARTBEAT_OK" in message:
        sys.exit(0)

    success = send_discord(message)
    if success:
        # Clear after successful send
        NOTIFICATION_FILE.write_text("")
    else:
        print(f"Failed to send notification")
        sys.exit(1)


if __name__ == "__main__":
    main()
