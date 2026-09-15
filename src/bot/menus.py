"""Menu builders with styled colored buttons.

All menus use the Bot API 9.4+ style attribute for colored buttons:
- primary (blue) for standard actions
- success (green) for confirm/start/approve
- danger (red) for stop/delete/cancel
"""

from typing import Optional

from aiogram.types import InlineKeyboardMarkup

from src.bot.buttons import Btn, G


def main_menu_kb(admin: bool = False) -> InlineKeyboardMarkup:
    """Main menu with styled buttons."""
    kb = InlineKeyboardMarkup(row_width=2)
    
    # Row 1: Core YouTube features
    kb.add(
        Btn.primary(f"{G['play']}  Connect YouTube", callback_data="connect_youtube"),
        Btn.primary(f"{G['folder']}  My Channels", callback_data="list_channels"),
    )
    
    # Row 2: Upload actions
    kb.add(
        Btn.primary(f"{G['upload']}  Upload Video", callback_data="upload_video"),
        Btn.primary(f"{G['stats']}  My Jobs", callback_data="list_jobs"),
    )
    
    # Row 3: Metadata
    kb.add(
        Btn.primary(f"{G['settings']}  Edit Metadata", callback_data="edit_metadata"),
        Btn.primary(f"{G['spark']}  Suggest Hashtags", callback_data="suggest_hashtags"),
    )
    
    # Row 4: Status
    kb.add(
        Btn.primary(f"{G['graph']}  Upload Status", callback_data="upload_status"),
        Btn.primary(f"{G['bolt']}  Bot Speed", callback_data="bot_speed"),
    )
    
    # Row 5: Support
    kb.add(
        Btn.success(f"{G['chat']}  Support", callback_data="support"),
        Btn.primary(f"{G['help']}  Help", callback_data="show_help"),
    )
    
    # Row 6: Admin (if admin)
    if admin:
        kb.add(
            Btn.danger(f"{G['crown']}  Admin Panel", callback_data="admin_panel"),
        )
    
    return kb


def channel_list_kb(channels: list) -> InlineKeyboardMarkup:
    """Channel selection menu."""
    kb = InlineKeyboardMarkup(row_width=1)
    
    for ch in channels:
        status = "🟢" if ch.get("active") else "🔴"
        kb.add(
            Btn.primary(
                f"{status} {ch['name']}",
                callback_data=f"select_channel:{ch['id']}"
            )
        )
    
    kb.add(
        Btn.success(f"{G['plus']}  Add Channel", callback_data="connect_youtube"),
        Btn.danger(f"{G['back']}  Back", callback_data="menu_main"),
    )
    
    return kb


def upload_confirm_kb(job_id: int) -> InlineKeyboardMarkup:
    """Upload confirmation menu."""
    kb = InlineKeyboardMarkup(row_width=2)
    
    kb.add(
        Btn.success(f"{G['ok']}  Confirm Upload", callback_data=f"confirm_upload:{job_id}"),
        Btn.danger(f"{G['no']}  Cancel", callback_data="cancel_upload"),
    )
    
    kb.add(
        Btn.primary(f"{G['settings']}  Edit Metadata", callback_data=f"edit_job_metadata:{job_id}"),
    )
    
    return kb


def job_status_kb(job_id: int, status: str) -> InlineKeyboardMarkup:
    """Job status menu with actions."""
    kb = InlineKeyboardMarkup(row_width=2)
    
    if status in ("pending", "processing"):
        kb.add(
            Btn.danger(f"{G['stop']}  Cancel Job", callback_data=f"cancel_job:{job_id}"),
        )
    
    kb.add(
        Btn.primary(f"{G['refresh']}  Refresh", callback_data=f"refresh_job:{job_id}"),
        Btn.primary(f"{G['back']}  Back", callback_data="list_jobs"),
    )
    
    return kb


def admin_kb() -> InlineKeyboardMarkup:
    """Admin panel with styled buttons."""
    kb = InlineKeyboardMarkup(row_width=2)
    
    # Stats & Users
    kb.add(
        Btn.primary(f"{G['graph']}  Statistics", callback_data="adm_stats"),
        Btn.primary(f"{G['users']}  Users", callback_data="adm_users"),
    )
    
    # Channels & Jobs
    kb.add(
        Btn.primary(f"{G['diamond']}  All Channels", callback_data="adm_channels"),
        Btn.success(f"{G['wallet']}  Payments", callback_data="adm_payments"),
    )
    
    # Broadcast & Security
    kb.add(
        Btn.success(f"{G['broadcast']}  Broadcast", callback_data="adm_broadcast"),
        Btn.danger(f"{G['ban']}  Ban/Unban", callback_data="adm_ban"),
    )
    
    # GitHub Backup
    kb.add(
        Btn.primary(f"{G['cog']}  GitHub Backup", callback_data="adm_github"),
        Btn.danger(f"{G['lock']}  Security", callback_data="adm_security"),
    )
    
    # Settings
    kb.add(
        Btn.danger(f"{G['warn']}  Maintenance", callback_data="adm_maint"),
        Btn.primary(f"{G['settings']}  Settings", callback_data="adm_settings"),
    )
    
    # Back
    kb.add(
        Btn.danger(f"{G['back']}  Main Menu", callback_data="menu_main"),
    )
    
    return kb


def confirm_kb(
    yes_cb: str,
    no_cb: str = "menu_main",
    yes_label: str = "Confirm",
    no_label: str = "Cancel"
) -> InlineKeyboardMarkup:
    """Generic confirmation keyboard."""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn.success(f"{G['ok']}  {yes_label}", callback_data=yes_cb),
        Btn.danger(f"{G['no']}  {no_label}", callback_data=no_cb),
    )
    return kb


def back_kb(target: str, label: str = "Back") -> InlineKeyboardMarkup:
    """Back button keyboard."""
    kb = InlineKeyboardMarkup()
    kb.add(
        Btn.danger(f"{G['back']}  {label}", callback_data=target)
    )
    return kb


def cancel_kb(callback_data: str = "cancel_operation") -> InlineKeyboardMarkup:
    """Cancel button keyboard."""
    kb = InlineKeyboardMarkup()
    kb.add(
        Btn.danger(f"{G['no']}  Cancel", callback_data=callback_data)
    )
    return kb
