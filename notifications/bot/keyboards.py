"""
Reply keyboards for the phone verification bot.

NOTE: Telegram does NOT support request_contact on InlineKeyboardButton.
Only ReplyKeyboardMarkup supports contact sharing — this is a platform
limitation, not an implementation choice.
"""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove


def phone_request_keyboard() -> ReplyKeyboardMarkup:
    """
    Reply keyboard with a single button that triggers Telegram's
    native contact-sharing UI. The phone number is sent as a
    message.contact object, NOT as plain text.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="\U0001f4de Share phone number", request_contact=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Tap the button below to share your phone number",
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    """Remove the reply keyboard after the flow is complete."""
    return ReplyKeyboardRemove()
