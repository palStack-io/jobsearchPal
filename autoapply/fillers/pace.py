"""Fill a form at a person's pace (2026-11-22).

The boards score how a form was filled (reCAPTCHA v3/Enterprise on Greenhouse, Ashby's own checks): a whole form set in
three seconds, values appearing without keystrokes, no pointer movement and no time on the page read as a bot, and
the site adds a verification step at Submit. So the filler reads the page first, moves to each field, clicks into it,
types short answers key by key at a varied speed, pastes long ones (people paste long answers too), and pauses
between fields and before Submit. A form takes a couple of minutes instead of seconds.

This is pacing only. It never solves a captcha, fakes a browser fingerprint or hides that a browser is automated; a
verification step that still appears is left to the person (Finish it here, or Chrome on their Mac).

AUTOAPPLY_FILL_PACE=fast turns it off (the tests set it); anything else, or nothing, is human.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from typing import Callable

PASTE_OVER = 300          # answers longer than this are pasted, the way people paste a prepared paragraph
READ_S = (1.5, 4.0)       # after the page loads, before the first field
BETWEEN_S = (0.4, 1.4)    # between fields
SUBMIT_S = (3.0, 8.0)     # a look over the form before Submit
CHAR_MS = (35, 140)       # per key
THINK_S = (0.3, 0.9)      # an occasional pause between words
THINK_P = 0.08


@dataclass
class Pace:
    human: bool
    rng: random.Random = field(default_factory=random.Random)
    sleep: Callable[[float], None] = time.sleep
    scale: float = 1.0    # tests pass 0: human behaviour (keys, moves) without the waiting

    @classmethod
    def fast(cls, sleep: Callable[[float], None] = time.sleep) -> "Pace":
        return cls(human=False, sleep=sleep)

    @classmethod
    def human_pace(cls, rng: random.Random | None = None, sleep: Callable[[float], None] = time.sleep,
                   scale: float = 1.0) -> "Pace":
        return cls(human=True, rng=rng or random.Random(), sleep=sleep, scale=scale)

    def _wait(self, span: tuple[float, float]) -> None:
        if self.human:
            self.sleep(self.rng.uniform(*span) * self.scale)

    def read(self) -> None:
        self._wait(READ_S)

    def between(self) -> None:
        self._wait(BETWEEN_S)

    def before_submit(self) -> None:
        self._wait(SUBMIT_S)

    def char_delay_ms(self) -> float:
        return self.rng.uniform(*CHAR_MS) if self.human else 0

    def pastes(self, text: str) -> bool:
        return self.human and len(text) > PASTE_OVER

    # ---- in the page ------------------------------------------------------------------------------

    def move_to(self, page, el) -> None:
        """Scroll the field into view and move the pointer to it, in steps, to a point inside it."""
        if not self.human:
            return
        try:
            el.scroll_into_view_if_needed(timeout=3000)
            box = el.bounding_box()
        except Exception:
            return
        if not box:
            return
        x = box["x"] + box["width"] * self.rng.uniform(0.25, 0.75)
        y = box["y"] + box["height"] * self.rng.uniform(0.3, 0.7)
        page.mouse.move(x, y, steps=self.rng.randint(8, 20))

    def type_into(self, page, el, text: str) -> None:
        """Click into the field, clear what's there (Lever and Ashby prefill from the resume), then type or paste."""
        if not self.human:
            el.fill(text)
            return
        self.move_to(page, el)
        el.click()
        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.press("Delete")
        if self.pastes(text):
            self._wait(THINK_S)
            page.keyboard.insert_text(text)
            return
        words = text.split(" ")
        for i, w in enumerate(words):
            chunk = w + (" " if i < len(words) - 1 else "")
            page.keyboard.type(chunk, delay=self.char_delay_ms() * self.scale)
            if self.rng.random() < THINK_P:
                self._wait(THINK_S)


def current() -> Pace:
    """The pace for this process: human unless AUTOAPPLY_FILL_PACE=fast."""
    return Pace.fast() if os.environ.get("AUTOAPPLY_FILL_PACE", "").strip().lower() == "fast" else Pace.human_pace()

