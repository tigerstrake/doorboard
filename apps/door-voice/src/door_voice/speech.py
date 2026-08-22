"""Local text-to-speech. Never leaves the Pi (ADR-0034)."""

from __future__ import annotations

import asyncio
import logging
import shutil

logger = logging.getLogger("door_voice.speech")

# Names come from the door's own enrolment records, but this text reaches a
# subprocess, so keep it to something obviously safe to pass as a single argv
# entry. No shell is ever used; this is belt-and-braces, not the only defence.
_MAX_TEXT_LEN = 120


def sanitize(text: str) -> str:
    """Collapse whitespace and drop control characters."""
    cleaned = " ".join(text.split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return cleaned[:_MAX_TEXT_LEN]


class Speaker:
    """Synthesises with piper when present, else espeak-ng.

    Both are invoked with an argv list (never a shell) and a hard timeout, so a
    wedged synthesiser can't pile processes up behind it.
    """

    def __init__(
        self,
        *,
        piper_binary: str,
        piper_voice: str,
        espeak_binary: str,
        aplay_binary: str,
        alsa_device: str,
        timeout_s: float,
    ) -> None:
        self._piper = piper_binary
        self._piper_voice = piper_voice
        self._espeak = espeak_binary
        self._aplay = aplay_binary
        self._alsa_device = alsa_device
        self._timeout_s = timeout_s

    def available_backend(self) -> str | None:
        """Which synthesiser this Pi actually has, if any."""
        if self._piper_voice and shutil.which(self._piper):
            return "piper"
        if shutil.which(self._espeak):
            return "espeak"
        return None

    async def speak(self, text: str) -> bool:
        """Say ``text``. Returns False on any failure — never raises.

        A failure here is cosmetic by construction: nothing downstream of the
        door depends on speech happening (ADR-0002).
        """
        clean = sanitize(text)
        if not clean:
            return False
        backend = self.available_backend()
        if backend is None:
            logger.warning("no_tts_backend", extra={"piper": self._piper, "espeak": self._espeak})
            return False
        try:
            if backend == "piper":
                return await self._speak_piper(clean)
            return await self._speak_espeak(clean)
        except (TimeoutError, OSError) as exc:
            logger.warning("speak_failed", extra={"backend": backend, "error": str(exc)})
            return False

    async def _run(self, argv: list[str], stdin_text: str | None = None) -> bool:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = stdin_text.encode() if stdin_text is not None else None
        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(input=payload), timeout=self._timeout_s
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            logger.warning(
                "tts_process_failed",
                extra={
                    "argv0": argv[0],
                    "returncode": proc.returncode,
                    "error": (stderr or b"").decode(errors="replace")[:200],
                },
            )
            return False
        return True

    def _aplay_argv(self) -> list[str]:
        argv = [self._aplay, "-q"]
        if self._alsa_device:
            argv += ["-D", self._alsa_device]
        return argv

    async def _speak_piper(self, text: str) -> bool:
        # piper writes a WAV to stdout; hand it straight to aplay rather than
        # via a temp file, so nothing lands on disk.
        piper = await asyncio.create_subprocess_exec(
            self._piper,
            "--model",
            self._piper_voice,
            "--output_file",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        aplay = await asyncio.create_subprocess_exec(
            *self._aplay_argv(),
            stdin=piper.stdout,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if piper.stdin is not None:
            piper.stdin.write(text.encode())
            await piper.stdin.drain()
            piper.stdin.close()
        try:
            await asyncio.wait_for(aplay.wait(), timeout=self._timeout_s)
        except TimeoutError:
            for proc in (piper, aplay):
                proc.kill()
            raise
        await piper.wait()
        return aplay.returncode == 0

    async def _speak_espeak(self, text: str) -> bool:
        # espeak-ng plays directly; -a/-s keep it from being startling.
        argv = [self._espeak, "-a", "130", "-s", "150"]
        if self._alsa_device:
            # espeak-ng has no -D; route via aplay by writing a WAV to stdout.
            argv += ["--stdout"]
            espeak = await asyncio.create_subprocess_exec(
                *argv,
                text,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            aplay = await asyncio.create_subprocess_exec(
                *self._aplay_argv(),
                stdin=espeak.stdout,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(aplay.wait(), timeout=self._timeout_s)
            except TimeoutError:
                for proc in (espeak, aplay):
                    proc.kill()
                raise
            await espeak.wait()
            return aplay.returncode == 0
        return await self._run([*argv, text])
