"""browser-use CLI subprocess wrapper."""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class BrowserError(Exception):
    """Error from browser-use CLI."""


class BrowserSession:
    """Wraps browser-use CLI commands via subprocess.

    Each method runs a single browser-use CLI command and returns parsed output.
    The browser daemon persists between commands (~50ms latency per call).
    """

    def __init__(
        self,
        headed: bool = False,
        session: str = "amz-scout",
        use_profile: bool = False,
    ) -> None:
        self._headed = headed
        self._session = session
        self._use_profile = use_profile

    def _base_args(self) -> list[str]:
        args = ["browser-use"]
        if self._use_profile:
            args.append("--profile")
        if self._headed:
            args.append("--headed")
        args.extend(["--session", self._session])
        return args

    def _run(self, cmd_args: list[str], timeout: int = 30, json_mode: bool = True) -> dict | str:
        """Run a browser-use CLI command and return parsed result."""
        base = self._base_args()
        if json_mode:
            base.append("--json")
        full_cmd = base + cmd_args

        logger.debug("Running: %s", " ".join(full_cmd))
        try:
            proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise BrowserError(f"Timeout ({timeout}s) running: {' '.join(cmd_args)}") from e

        if proc.returncode != 0:
            error_msg = proc.stderr.strip() or proc.stdout.strip()
            raise BrowserError(f"browser-use error (rc={proc.returncode}): {error_msg}")

        output = proc.stdout.strip()
        if not output:
            return {} if json_mode else ""

        if json_mode:
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return {"raw": output}
        return output

    def _run_simple(self, cmd_args: list[str], timeout: int = 30) -> str:
        """Run command without JSON mode, return raw text."""
        return self._run(cmd_args, timeout=timeout, json_mode=False)  # type: ignore[return-value]

    def open(self, url: str, timeout: int = 30) -> None:
        """Navigate to URL."""
        self._run_simple(["open", url], timeout=timeout)

    def evaluate(self, js: str, timeout: int = 30) -> dict:
        """Execute JavaScript and return parsed JSON result."""
        result = self._run(["eval", js], timeout=timeout)
        if isinstance(result, dict) and result.get("success") and "data" in result:
            raw = result["data"].get("result", "")
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return {"value": raw}
            return {"value": raw}
        return result if isinstance(result, dict) else {"value": result}

    def state(self, timeout: int = 15) -> dict:
        """Get browser state (URL, title, clickable elements)."""
        result = self._run(["state"], timeout=timeout)
        return result if isinstance(result, dict) else {}

    def click(self, index: int, timeout: int = 10) -> None:
        """Click element by index."""
        self._run_simple(["click", str(index)], timeout=timeout)

    def type_text(self, text: str, timeout: int = 10) -> None:
        """Type text into focused element."""
        self._run_simple(["type", text], timeout=timeout)

    def input_to(self, index: int, text: str, timeout: int = 10) -> None:
        """Click element then type text."""
        self._run_simple(["input", str(index), text], timeout=timeout)

    def keys(self, key: str, timeout: int = 10) -> None:
        """Send keyboard keys (e.g., 'Enter', 'Escape')."""
        self._run_simple(["keys", key], timeout=timeout)

    def scroll(self, direction: str = "down", amount: int = 500, timeout: int = 10) -> None:
        """Scroll page."""
        self._run_simple(["scroll", direction, "--amount", str(amount)], timeout=timeout)

    def screenshot(self, path: str, timeout: int = 15) -> None:
        """Take screenshot and save to path."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._run_simple(["screenshot", path], timeout=timeout)

    def close(self) -> None:
        """Close browser and stop daemon."""
        try:
            self._run_simple(["close"], timeout=10)
        except BrowserError:
            pass  # Already closed


def check_browser_use_installed() -> bool:
    """Check if browser-use CLI is available."""
    try:
        result = subprocess.run(
            ["browser-use", "doctor"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
