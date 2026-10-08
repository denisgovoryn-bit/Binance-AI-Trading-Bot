"""Small onboarding and settings keyboards."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def market_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Spot + Futures", callback_data="markets:BOTH")],
            [
                InlineKeyboardButton(text="Spot only", callback_data="markets:SPOT"),
                InlineKeyboardButton(text="Futures only", callback_data="markets:FUTURES"),
            ],
        ]
    )


def reset_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Confirm virtual reset", callback_data="reset:CONFIRM")],
            [InlineKeyboardButton(text="Cancel", callback_data="reset:CANCEL")],
        ]
    )
