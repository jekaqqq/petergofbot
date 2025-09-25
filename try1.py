import os
from dotenv import load_dotenv
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
)

from database import get_connection

# Загружаем .env
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

# Состояния
SHOP_CATEGORY, SHOP_BRAND, SHOP_VARIANTS = range(3)


# ------------------- START -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_connection()
    categories = conn.execute("SELECT * FROM categories").fetchall()
    conn.close()

    keyboard = [
        [InlineKeyboardButton(cat["name"], callback_data=f"cat_{cat['id']}")]
        for cat in categories
    ]

    if update.message:
        await update.message.reply_text(
            "📂 Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    elif update.callback_query:
        query = update.callback_query
        await query.edit_message_text(
            "📂 Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    return SHOP_CATEGORY


# ------------------- CATEGORY -------------------
async def shop_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[1])
    context.user_data["cat_id"] = cat_id

    conn = get_connection()
    category = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    products = conn.execute("SELECT * FROM products WHERE category_id=?", (cat_id,)).fetchall()
    conn.close()

    if not products:
        await query.edit_message_text(
            f"❌ В категории «{category['name']}» пока нет товаров.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Назад", callback_data="back_categories")]]
            ),
        )
        return SHOP_CATEGORY

    keyboard = [
        [InlineKeyboardButton(prod["brand"], callback_data=f"brand_{prod['id']}")]
        for prod in products
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_categories")])

    await query.edit_message_text(
        f"📦 Товары из категории «{category['name']}»:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SHOP_BRAND


# ------------------- BRAND -------------------
async def shop_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prod_id = int(query.data.split("_")[1])
    context.user_data["prod_id"] = prod_id

    conn = get_connection()
    product = conn.execute("SELECT * FROM products WHERE id=?", (prod_id,)).fetchone()
    category = conn.execute(
        "SELECT * FROM categories WHERE id=?", (product["category_id"],)
    ).fetchone()
    variants = conn.execute(
        "SELECT * FROM variants WHERE product_id=?", (prod_id,)
    ).fetchall()
    conn.close()

    option_label = "Цвет" if category["option_type"] == "color" else "Крепость"

    if not variants:
        await query.edit_message_text(
            f"❌ У товара «{product['brand']}» пока нет вариантов.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Назад", callback_data=f"back_cat_{product['category_id']}")]]
            ),
        )
        return SHOP_BRAND

    keyboard = [
        [InlineKeyboardButton(f"{v['option']} — {v['price']}₽", callback_data=f"var_{v['id']}")]
        for v in variants
    ]
    keyboard.append(
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_cat_{product['category_id']}")]
    )

    await query.edit_message_text(
        f"🔹 {product['brand']} ({option_label}):",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SHOP_VARIANTS


# ------------------- VARIANT -------------------
async def shop_variant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    var_id = int(query.data.split("_")[1])

    conn = get_connection()
    variant = conn.execute(
        """
        SELECT v.*, p.brand, p.category_id 
        FROM variants v 
        JOIN products p ON v.product_id = p.id 
        WHERE v.id=?
    """,
        (var_id,),
    ).fetchone()
    conn.close()

    option_label = "Цвет" if context.user_data.get("option_type") == "color" else "Крепость"

    caption = (
        f"📦 {variant['brand']}\n"
        f"🔹 {option_label}: {variant['option']}\n"
        f"💰 Цена: {variant['price']}₽\n"
        f"📦 В наличии: {variant['stock']}"
    )

    keyboard = [
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_brand_{variant['product_id']}")]
    ]

    if variant["image_id"]:
        try:
            await query.edit_message_media(
                InputMediaPhoto(media=variant["image_id"], caption=caption),
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except:
            await query.edit_message_caption(
                caption, reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        await query.edit_message_text(
            caption, reply_markup=InlineKeyboardMarkup(keyboard)
        )

    return SHOP_VARIANTS


# ------------------- BACK BUTTONS -------------------
async def back_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start(update, context)


async def back_to_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_id = int(query.data.split("_")[2])  # back_cat_5
    context.user_data["cat_id"] = cat_id
    return await shop_category(update, context)


async def back_to_variants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    prod_id = int(query.data.split("_")[2])  # back_brand_7
    context.user_data["prod_id"] = prod_id
    return await shop_brand(update, context)


# ------------------- MAIN -------------------
def main():
    app = Application.builder().token(TOKEN).build()

    shop_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SHOP_CATEGORY: [
                CallbackQueryHandler(shop_category, pattern="^cat_"),
            ],
            SHOP_BRAND: [
                CallbackQueryHandler(shop_brand, pattern="^brand_"),
                CallbackQueryHandler(back_to_categories, pattern="^back_categories$"),
            ],
            SHOP_VARIANTS: [
                CallbackQueryHandler(shop_variant, pattern="^var_"),
                CallbackQueryHandler(back_to_brands, pattern="^back_cat_"),
                CallbackQueryHandler(back_to_variants, pattern="^back_brand_"),
            ],
        },
        fallbacks=[],
    )

    app.add_handler(shop_conv)
    app.run_polling()


if __name__ == "__main__":
    main()
