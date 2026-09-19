"""Colored inline buttons with Bot API 9.4+ style support.

Button Styles:
- style="primary"  → 🔵 Blue (default for most actions)
- style="success"  → 🟢 Green (for confirm, start, approve)
- style="danger"   → 🔴 Red (for stop, delete, cancel)

Note: Telegram client must support Bot API 9.4+ for colors to show.
Falls back to normal buttons on older clients.
"""

from aiogram.types import InlineKeyboardButton
from typing import Optional


class StyledButton(InlineKeyboardButton):
    """InlineKeyboardButton with optional color style support.
    
    Usage:
        button = StyledButton("Click Me", callback_data="action", style="success")
        button = StyledButton("Delete", callback_data="delete", style="danger")
        button = StyledButton("Info", callback_data="info", style="primary")
    """
    
    def __init__(
        self,
        text: str,
        callback_data: Optional[str] = None,
        url: Optional[str] = None,
        style: str = "",
        **kwargs
    ):
        super().__init__(text=text, callback_data=callback_data, url=url, **kwargs)
        # Style attribute for Bot API 9.4+ colored buttons
        if style:
            self.style = style  # type: ignore[attr-defined]

    def to_dict(self):
        """Convert to dict with style field for Telegram API."""
        d = super().to_dict()
        if getattr(self, "style", ""):
            d["style"] = self.style
        return d


# ═════════════════════════════════════════════════════════════════
#  BUTTON PRESETS - Reusable styled buttons
# ═════════════════════════════════════════════════════════════════

class Btn:
    """Button factory with style presets."""
    
    @staticmethod
    def primary(text: str, **kwargs) -> StyledButton:
        """Blue button - default for most actions."""
        return StyledButton(text, style="primary", **kwargs)
    
    @staticmethod
    def success(text: str, **kwargs) -> StyledButton:
        """Green button - for confirm, start, approve."""
        return StyledButton(text, style="success", **kwargs)
    
    @staticmethod
    def danger(text: str, **kwargs) -> StyledButton:
        """Red button - for stop, delete, cancel."""
        return StyledButton(text, style="danger", **kwargs)


# ═════════════════════════════════════════════════════════════════
#  GLYPH ICONS - Beautiful symbols for buttons
# ═════════════════════════════════════════════════════════════════

G = {
    # Status & Decisions
    "ok": "✓",
    "no": "✘",
    "warn": "⚠",
    "arrow": "→",
    "bullet": "•",
    "tri": "▸",
    "diamond": "◆",
    "star": "★",
    "spark": "✦",
    "back": "↲",
    "fwd": "▶",
    "plus": "⊕",
    "minus": "⊖",
    "rec": "◉",
    "rec_off": "○",
    
    # Process State
    "play": "‣",
    "stop": "■",
    "pause": "❙❙",
    "refresh": "↻",
    "running": "▶",
    "stopped": "■",
    
    # Security
    "lock": "▣",
    "unlock": "▢",
    "secure": "◈",
    "key": "❖",
    "shield": "◇",
    "ban": "⚔",
    "trash": "✖",
    "eye": "◉",
    
    # People
    "user": "◈",
    "users": "◎",
    "crown": "♔",
    
    # Money
    "wallet": "◆",
    "premium": "⌬",
    "lifetime": "✶",
    "gift": "✦",
    "trophy": "★",
    
    # Data
    "graph": "▪",
    "stats": "▪",
    "chart_up": "▲",
    "plan": "▤",
    
    # Comms
    "broadcast": "⚑",
    "chat": "▫",
    
    # Storage
    "folder": "▸",
    "upload": "▴",
    "download": "▾",
    "cloud": "☁",
    
    # Tools
    "settings": "⚙",
    "cog": "⚙",
    "bolt": "⚡",
    "clock": "⏱",
    
    # Dividers
    "div": "━" * 16,
    "div_eq": "═" * 16,
}


def divider(width: int = 22, ch: str = "━") -> str:
    """Create a divider line."""
    return ch * width


def bullet(label: str, value: str, glyph: str = "•") -> str:
    """Create a bullet point with label and value."""
    return f"{glyph}  <b>{label}</b>: <code>{value}</code>"
