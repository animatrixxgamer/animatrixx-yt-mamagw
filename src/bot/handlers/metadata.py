"""Metadata editing handlers."""

from datetime import datetime
from typing import Any

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.hashtag import hashtag_service

router = Router()


class MetadataStates(StatesGroup):
    """FSM states for metadata editing."""
    EDITING_TITLE = State()
    EDITING_DESCRIPTION = State()
    EDITING_TAGS = State()
    EDITING_HASHTAGS = State()
    EDITING_PRIVACY = State()
    EDITING_SCHEDULE = State()
    EDITING_CATEGORY = State()
    SUGGESTING_HASHTAGS = State()


@router.callback_query(F.data == "edit_metadata")
async def edit_metadata(callback: CallbackQuery, state: FSMContext) -> None:
    """Show metadata editing menu."""
    data = await state.get_data()
    metadata = data.get("metadata", {})

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📝 Title: {metadata.get('title', 'Not set')[:30]}",
                    callback_data="edit_field:title",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"📄 Description: {'Set' if metadata.get('description') else 'Not set'}",
                    callback_data="edit_field:description",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🏷️ Tags: {metadata.get('tags', 'Not set')[:30]}",
                    callback_data="edit_field:tags",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"#️⃣ Hashtags: {metadata.get('hashtags', 'Not set')[:30]}",
                    callback_data="edit_field:hashtags",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🔒 Privacy: {metadata.get('privacy_status', 'private')}",
                    callback_data="edit_field:privacy",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"📅 Schedule: {metadata.get('scheduled_at', 'Not scheduled')}",
                    callback_data="edit_field:schedule",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💡 Suggest Hashtags",
                    callback_data="suggest_hashtags",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Done Editing",
                    callback_data="finish_editing",
                ),
            ],
        ]
    )

    await callback.message.edit_text(
        "📝 <b>Edit Upload Metadata</b>\n\n"
        "Click on a field to edit it. Current values are shown.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_field:"))
async def edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    """Start editing a specific field."""
    field = callback.data.split(":")[1]

    prompts = {
        "title": "📝 Enter video title (max 100 chars):",
        "description": "📄 Enter video description (max 5000 chars):",
        "tags": "🏷️ Enter tags (comma-separated):",
        "hashtags": "#️⃣ Enter hashtags (comma-separated, without #):",
        "privacy": "🔒 Select privacy status:",
        "schedule": "📅 Enter schedule time (YYYY-MM-DD HH:MM) or 'now' to publish immediately:",
    }

    if field == "privacy":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔒 Private", callback_data="set_privacy:private"),
                    InlineKeyboardButton(text="🌐 Public", callback_data="set_privacy:public"),
                ],
                [
                    InlineKeyboardButton(text="🔗 Unlisted", callback_data="set_privacy:unlisted"),
                ],
            ]
        )
        await callback.message.edit_text(prompts[field], reply_markup=keyboard)
    else:
        state_map = {
            "title": MetadataStates.EDITING_TITLE,
            "description": MetadataStates.EDITING_DESCRIPTION,
            "tags": MetadataStates.EDITING_TAGS,
            "hashtags": MetadataStates.EDITING_HASHTAGS,
            "schedule": MetadataStates.EDITING_SCHEDULE,
        }
        await state.set_state(state_map[field])
        await callback.message.edit_text(prompts[field])

    await callback.answer()


@router.message(MetadataStates.EDITING_TITLE)
async def save_title(message: Message, state: FSMContext) -> None:
    """Save title."""
    title = message.text[:100] if message.text else ""
    data = await state.get_data()
    metadata = data.get("metadata", {})
    metadata["title"] = title
    await state.update_data(metadata=metadata)
    await message.answer(f"✅ Title saved: {title}")
    await state.set_state(None)


@router.message(MetadataStates.EDITING_DESCRIPTION)
async def save_description(message: Message, state: FSMContext) -> None:
    """Save description."""
    description = message.text[:5000] if message.text else ""
    data = await state.get_data()
    metadata = data.get("metadata", {})
    metadata["description"] = description
    await state.update_data(metadata=metadata)
    await message.answer("✅ Description saved!")
    await state.set_state(None)


@router.message(MetadataStates.EDITING_TAGS)
async def save_tags(message: Message, state: FSMContext) -> None:
    """Save tags."""
    tags = message.text if message.text else ""
    data = await state.get_data()
    metadata = data.get("metadata", {})
    metadata["tags"] = tags
    await state.update_data(metadata=metadata)
    await message.answer(f"✅ Tags saved: {tags}")
    await state.set_state(None)


@router.message(MetadataStates.EDITING_HASHTAGS)
async def save_hashtags(message: Message, state: FSMContext) -> None:
    """Save hashtags."""
    hashtags = message.text if message.text else ""
    # Normalize hashtags
    tags_list = [t.strip() for t in hashtags.split(",") if t.strip()]
    normalized = hashtag_service.normalize_hashtags(tags_list)
    formatted = ", ".join(normalized)

    data = await state.get_data()
    metadata = data.get("metadata", {})
    metadata["hashtags"] = formatted
    await state.update_data(metadata=metadata)
    await message.answer(f"✅ Hashtags saved: {formatted}")
    await state.set_state(None)


@router.callback_query(F.data.startswith("set_privacy:"))
async def save_privacy(callback: CallbackQuery, state: FSMContext) -> None:
    """Save privacy status."""
    privacy = callback.data.split(":")[1]
    data = await state.get_data()
    metadata = data.get("metadata", {})
    metadata["privacy_status"] = privacy
    await state.update_data(metadata=metadata)
    await callback.message.edit_text(f"✅ Privacy set to: {privacy}")
    await state.set_state(None)
    await callback.answer()


@router.message(MetadataStates.EDITING_SCHEDULE)
async def save_schedule(message: Message, state: FSMContext) -> None:
    """Save schedule time."""
    text = message.text if message.text else ""

    if text.lower() == "now":
        scheduled_at = None
    else:
        try:
            scheduled_at = datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            await message.answer(
                "❌ Invalid format. Use YYYY-MM-DD HH:MM or 'now'"
            )
            return

    data = await state.get_data()
    metadata = data.get("metadata", {})
    metadata["scheduled_at"] = scheduled_at.isoformat() if scheduled_at else None
    await state.update_data(metadata=metadata)

    if scheduled_at:
        await message.answer(f"✅ Scheduled for: {scheduled_at}")
    else:
        await message.answer("✅ Will publish immediately")
    await state.set_state(None)


@router.callback_query(F.data == "suggest_hashtags")
async def suggest_hashtags(callback: CallbackQuery, state: FSMContext) -> None:
    """Get hashtag suggestions."""
    data = await state.get_data()
    metadata = data.get("metadata", {})

    suggestions = await hashtag_service.suggest_hashtags(
        title=metadata.get("title", ""),
        description=metadata.get("description", ""),
        count=10,
    )

    suggestions_text = ", ".join(suggestions) if suggestions else "No suggestions available"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Use These",
                    callback_data=f"apply_suggestions:{suggestions_text}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Get More",
                    callback_data="suggest_hashtags",
                ),
            ],
        ]
    )

    await callback.message.edit_text(
        "💡 <b>Hashtag Suggestions</b>\n\n"
        f"{suggestions_text}\n\n"
        "These are suggestions based on your content. "
        "Customize them for your specific video!",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("apply_suggestions:"))
async def apply_suggestions(callback: CallbackQuery, state: FSMContext) -> None:
    """Apply suggested hashtags."""
    suggestions = callback.data.split(":", 1)[1]
    data = await state.get_data()
    metadata = data.get("metadata", {})
    metadata["hashtags"] = suggestions
    await state.update_data(metadata=metadata)
    await callback.message.edit_text(f"✅ Hashtags applied: {suggestions}")
    await callback.answer()


@router.callback_query(F.data == "finish_editing")
async def finish_editing(callback: CallbackQuery, state: FSMContext) -> None:
    """Finish metadata editing."""
    await state.set_state(None)
    await callback.message.edit_text(
        "✅ <b>Metadata Editing Complete</b>\n\n"
        "Click 'Upload Now' to proceed with the upload."
    )
    await callback.answer()
