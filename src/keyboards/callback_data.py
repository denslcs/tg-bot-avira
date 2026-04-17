"""
Единое место для callback_data inline-кнопок (главное меню, оплата, генерация).

Строки не дублируем в хендлерах — импортируем отсюда, чтобы не разъезжались фильтры и клавиатуры.
"""

# --- Главный экран / навигация (handlers/commands.py) ---
CB_MENU_REF = "ref_menu"
CB_MENU_REF_LEGACY = "menu:ref"
CB_MENU_PROFILE = "menu:profile"
CB_MENU_ABOUT = "menu:about"
CB_MENU_PAY = "menu:pay"
CB_MENU_SUPPORT = "menu:support"
CB_MENU_HUB = "menu:hub"
CB_MENU_FAQ = "menu:faq"
CB_MENU_CHANNEL = "menu:channel"
CB_MENU_MELLSTROY = "menu:mellstroy"
CB_MENU_PROFILE_HUB = "menu:profile_hub"
CB_MENU_ABOUT_HUB = "menu:about_hub"
CB_MENU_PAY_HUB = "menu:pay_hub"
CB_MENU_REF_HUB = "menu:ref_hub"
CB_MENU_SUPPORT_HUB = "menu:support_hub"
CB_MENU_FAQ_HUB = "menu:faq_hub"
CB_MENU_CHANNEL_HUB = "menu:channel_hub"

# --- Генерация изображений (handlers/img_commands.py) ---
CB_CREATE_IMAGE = "menu:create_image"
CB_CREATE_IMAGE_HUB = "menu:create_image_hub"
CB_MENU_BACK_START = "menu:back_start"
CB_BACK_IMAGE_MODELS = "img:back_models"
CB_GEN_TEXT = "img:mode:text"
CB_GEN_EDIT = "img:mode:edit"
CB_PICK_NANO = "img:pick_nano"
CB_PICK_NANO_2 = "img:pick_nano2"
CB_PICK_QWEN = "img:pick_qwen"
CB_PICK_FLUX = "img:pick_flux"
CB_READY_IDEAS = "menu:ready_ideas"
CB_READY_IDEAS_HUB = "menu:ready_ideas_hub"
CB_APPLY_READY_PREFIX = "img:idea:"
CB_READY_CAT_PREFIX = "img:idea_cat:"
CB_READY_NAV_PREFIX = "img:idea_nav:"
CB_READY_BEARD_SIZE_PREFIX = "img:idea_beard_size:"
CB_READY_PHOTO_BACK = "img:idea_photo_back"
CB_READY_CONFIRM = "img:idea_confirm"
CB_REGEN = "img:regen"
CB_REGEN_READY_REDO = "img:regen_ready_redo"
CB_BACK_TO_READY_IDEAS = "img:back_ready_ideas"
CB_IMG_OK = "img:ok"
CB_IMG_CANCEL = "img:cancel"
# Выбор модели для подписчиков: img:m:0, img:m:1, ...
CB_IMG_MODEL_SEL_PREFIX = "img:m:"

# --- Оплата (handlers/payments.py) ---
CB_PAY_MENU = "pay:menu"
CB_PAY_MENU_HUB = "pay:menu_hub"
CB_PAY_BONUS_MENU = "pay:bonus_menu"
CB_PAY_BONUS_MENU_HUB = "pay:bonus_menu_hub"
CB_PAY_PLAN_PREFIX = "pay:p:"
CB_PAY_PACK_PREFIX = "pay:b:"
CB_PAY_STARS_PREFIX = "pay:s:"
CB_PAY_RUB_PREFIX = "pay:r:"
CB_PAY_INTL_PREFIX = "pay:i:"
CB_PAY_CRYPTO_PREFIX = "pay:c:"

__all__ = [
    "CB_APPLY_READY_PREFIX",
    "CB_READY_CAT_PREFIX",
    "CB_READY_NAV_PREFIX",
    "CB_READY_BEARD_SIZE_PREFIX",
    "CB_READY_PHOTO_BACK",
    "CB_READY_CONFIRM",
    "CB_BACK_IMAGE_MODELS",
    "CB_CREATE_IMAGE",
    "CB_GEN_EDIT",
    "CB_GEN_TEXT",
    "CB_IMG_CANCEL",
    "CB_IMG_MODEL_SEL_PREFIX",
    "CB_IMG_OK",
    "CB_MENU_ABOUT",
    "CB_MENU_BACK_START",
    "CB_MENU_CHANNEL",
    "CB_MENU_MELLSTROY",
    "CB_MENU_FAQ",
    "CB_MENU_HUB",
    "CB_MENU_PAY",
    "CB_MENU_PROFILE",
    "CB_MENU_PROFILE_HUB",
    "CB_MENU_REF",
    "CB_MENU_REF_HUB",
    "CB_MENU_REF_LEGACY",
    "CB_MENU_SUPPORT",
    "CB_MENU_SUPPORT_HUB",
    "CB_MENU_ABOUT_HUB",
    "CB_MENU_PAY_HUB",
    "CB_MENU_FAQ_HUB",
    "CB_MENU_CHANNEL_HUB",
    "CB_PAY_BONUS_MENU",
    "CB_PAY_BONUS_MENU_HUB",
    "CB_PAY_CRYPTO_PREFIX",
    "CB_PAY_INTL_PREFIX",
    "CB_PAY_MENU",
    "CB_PAY_MENU_HUB",
    "CB_PAY_PACK_PREFIX",
    "CB_PAY_PLAN_PREFIX",
    "CB_PAY_RUB_PREFIX",
    "CB_PAY_STARS_PREFIX",
    "CB_PICK_NANO",
    "CB_PICK_NANO_2",
    "CB_PICK_QWEN",
    "CB_PICK_FLUX",
    "CB_CREATE_IMAGE_HUB",
    "CB_READY_IDEAS",
    "CB_READY_IDEAS_HUB",
    "CB_REGEN",
    "CB_REGEN_READY_REDO",
    "CB_BACK_TO_READY_IDEAS",
]
