
import os
import re
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import (
    create_engine, String, Integer, BigInteger, Text, ForeignKey,
    DateTime, Numeric, Boolean, func, select
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.dialects.postgresql import JSONB

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("carpet-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./carpet_bot.db")
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_person: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    cooperation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    carpets: Mapped[list["Carpet"]] = relationship(back_populates="supplier")


class Carpet(Base):
    __tablename__ = "carpets"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False, index=True)

    code: Mapped[str | None] = mapped_column(String(100), index=True)
    design: Mapped[str | None] = mapped_column(String(200))
    color: Mapped[str | None] = mapped_column(String(200))
    shaneh: Mapped[int | None] = mapped_column(Integer)
    density: Mapped[int | None] = mapped_column(Integer)
    yarn_material: Mapped[str | None] = mapped_column(String(200))
    size: Mapped[str | None] = mapped_column(String(100))

    purchase_price: Mapped[int | None] = mapped_column(BigInteger)
    suggested_sale_price: Mapped[int | None] = mapped_column(BigInteger)
    stock: Mapped[int | None] = mapped_column(Integer)

    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    supplier: Mapped["Supplier"] = relationship(back_populates="carpets")
    media: Mapped[list["CarpetMedia"]] = relationship(
        back_populates="carpet",
        cascade="all, delete-orphan",
        order_by="CarpetMedia.sort_order",
    )


class CarpetMedia(Base):
    __tablename__ = "carpet_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    carpet_id: Mapped[int] = mapped_column(ForeignKey("carpets.id"), nullable=False, index=True)

    media_type: Mapped[str] = mapped_column(String(20), nullable=False)  # photo/video
    telegram_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    carpet: Mapped["Carpet"] = relationship(back_populates="media")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    active_supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def init_db():
    Base.metadata.create_all(bind=engine)


def get_or_create_session(db, telegram_user_id: int) -> UserSession:
    session = db.scalar(
        select(UserSession).where(UserSession.telegram_user_id == telegram_user_id)
    )
    if session is None:
        session = UserSession(telegram_user_id=telegram_user_id)
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


def set_active_supplier(telegram_user_id: int, supplier_id: int):
    with SessionLocal() as db:
        session = get_or_create_session(db, telegram_user_id)
        session.active_supplier_id = supplier_id
        db.commit()


def get_active_supplier(telegram_user_id: int):
    with SessionLocal() as db:
        session = db.scalar(
            select(UserSession).where(UserSession.telegram_user_id == telegram_user_id)
        )
        if not session or not session.active_supplier_id:
            return None
        return db.get(Supplier, session.active_supplier_id)


def fa_to_en(value: str) -> str:
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return value.translate(table)


def parse_int(value: str):
    value = fa_to_en(value).replace(",", "").replace("٬", "").strip()
    if not re.fullmatch(r"-?\d+", value):
        return None
    return int(value)


def optional_text(text: str) -> str | None:
    text = text.strip()
    if text in {"⏭ نامشخص", "نامشخص", "ندارم"}:
        return None
    return text


def main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🏪 تأمین‌کننده جدید", "➕ ثبت فرش"],
            ["📋 فرش‌های ثبت‌شده", "📊 گزارش"],
        ],
        resize_keyboard=True,
    )


SUP_NAME, SUP_PERSON, SUP_PHONE, SUP_CITY, SUP_ADDRESS, SUP_COOP, SUP_CONFIRM = range(7)
CAR_PHOTOS, CAR_VIDEOS, CAR_CODE, CAR_DESIGN, CAR_COLOR, CAR_SHANEH, CAR_DENSITY, CAR_YARN, CAR_SIZE, CAR_BUY, CAR_SELL, CAR_STOCK, CAR_DESC, CAR_CONFIRM, CAR_EDIT = range(20, 35)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\n\n"
        "این ربات برای جمع‌آوری اطلاعات تأمین‌کنندگان و فرش‌هاست.\n"
        "اطلاعات ثبت‌شده در دیتابیس ذخیره می‌شود.",
        reply_markup=main_keyboard(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in ("supplier_draft", "carpet_draft"):
        context.user_data.pop(key, None)
    await update.message.reply_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


def unknown_keyboard():
    return ReplyKeyboardMarkup([["⏭ نامشخص"], ["❌ لغو"]], resize_keyboard=True)


async def start_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["supplier_draft"] = {}
    await update.message.reply_text(
        "🏪 ثبت تأمین‌کننده\n\n1/6\nنام فروشگاه / شرکت / کارخانه:",
        reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True),
    )
    return SUP_NAME


async def sup_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    value = update.message.text.strip()
    if not value:
        await update.message.reply_text("نام نمی‌تواند خالی باشد.")
        return SUP_NAME
    context.user_data["supplier_draft"]["name"] = value
    await update.message.reply_text("2/6\nنام شخص رابط:", reply_markup=unknown_keyboard())
    return SUP_PERSON


async def sup_person(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["supplier_draft"]["contact_person"] = optional_text(update.message.text)
    await update.message.reply_text("3/6\nشماره تماس:", reply_markup=unknown_keyboard())
    return SUP_PHONE


async def sup_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["supplier_draft"]["phone"] = optional_text(update.message.text)
    await update.message.reply_text("4/6\nشهر:", reply_markup=unknown_keyboard())
    return SUP_CITY


async def sup_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["supplier_draft"]["city"] = optional_text(update.message.text)
    await update.message.reply_text("5/6\nآدرس:", reply_markup=unknown_keyboard())
    return SUP_ADDRESS


async def sup_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["supplier_draft"]["address"] = optional_text(update.message.text)
    await update.message.reply_text(
        "6/6\nنوع همکاری / شرایط مهم همکاری:",
        reply_markup=unknown_keyboard(),
    )
    return SUP_COOP


async def sup_coop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["supplier_draft"]["cooperation"] = optional_text(update.message.text)
    d = context.user_data["supplier_draft"]

    summary = (
        "📋 بررسی تأمین‌کننده\n\n"
        f"🏪 {d['name']}\n"
        f"👤 شخص رابط: {d.get('contact_person') or 'نامشخص'}\n"
        f"📱 تلفن: {d.get('phone') or 'نامشخص'}\n"
        f"🏙 شهر: {d.get('city') or 'نامشخص'}\n"
        f"📍 آدرس: {d.get('address') or 'نامشخص'}\n"
        f"🤝 همکاری: {d.get('cooperation') or 'نامشخص'}\n\n"
        "اطلاعات درست است؟"
    )
    await update.message.reply_text(
        summary,
        reply_markup=ReplyKeyboardMarkup(
            [["✅ ثبت نهایی"], ["❌ لغو"]],
            resize_keyboard=True,
        ),
    )
    return SUP_CONFIRM


async def sup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ لغو":
        return await cancel(update, context)
    if text != "✅ ثبت نهایی":
        await update.message.reply_text("یکی از گزینه‌های زیر را انتخاب کن.")
        return SUP_CONFIRM

    d = context.user_data["supplier_draft"]
    user_id = update.effective_user.id

    with SessionLocal() as db:
        supplier = Supplier(
            name=d["name"],
            contact_person=d.get("contact_person"),
            phone=d.get("phone"),
            city=d.get("city"),
            address=d.get("address"),
            cooperation=d.get("cooperation"),
        )
        db.add(supplier)
        db.commit()
        db.refresh(supplier)
        supplier_id = supplier.id

    set_active_supplier(user_id, supplier_id)
    context.user_data.pop("supplier_draft", None)

    await update.message.reply_text(
        f"✅ تأمین‌کننده ثبت شد.\n\n"
        f"🏪 {d['name']}\n"
        f"شناسه: {supplier_id}\n\n"
        "این تأمین‌کننده اکنون تأمین‌کننده فعال است و فرش‌های بعدی به آن متصل می‌شوند.",
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


def carpet_start_keyboard():
    return ReplyKeyboardMarkup([["⏭ بدون عکس"], ["❌ لغو"]], resize_keyboard=True)


async def start_carpet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    supplier = get_active_supplier(update.effective_user.id)
    if not supplier:
        await update.message.reply_text(
            "⚠️ ابتدا یک تأمین‌کننده ثبت و فعال کن.",
            reply_markup=main_keyboard(),
        )
        return ConversationHandler.END

    context.user_data["carpet_draft"] = {
        "supplier_id": supplier.id,
        "photos": [],
        "videos": [],
    }

    await update.message.reply_text(
        f"➕ ثبت فرش\n\n"
        f"تأمین‌کننده فعال: {supplier.name}\n\n"
        "📷 عکس‌های فرش را بفرست.\n"
        "می‌توانی یک یا چند عکس از دوربین یا گالری بفرستی.\n"
        "وقتی تمام شد، «⏭ عکس‌ها تمام شد» را بزن.",
        reply_markup=ReplyKeyboardMarkup(
            [["⏭ عکس‌ها تمام شد"], ["⏭ بدون عکس"], ["❌ لغو"]],
            resize_keyboard=True,
        ),
    )
    return CAR_PHOTOS


async def car_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = context.user_data["carpet_draft"]["photos"]
    photo = update.message.photo[-1]
    photos.append({
        "media_type": "photo",
        "file_id": photo.file_id,
        "unique_id": photo.file_unique_id,
    })
    await update.message.reply_text(
        f"📷 عکس دریافت شد. تعداد عکس‌ها: {len(photos)}\n"
        "عکس بعدی را بفرست یا «⏭ عکس‌ها تمام شد» را بزن."
    )
    return CAR_PHOTOS


async def car_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ لغو":
        return await cancel(update, context)
    if text not in {"⏭ عکس‌ها تمام شد", "⏭ بدون عکس"}:
        await update.message.reply_text(
            "لطفاً عکس بفرست یا «⏭ عکس‌ها تمام شد» را انتخاب کن."
        )
        return CAR_PHOTOS

    await update.message.reply_text(
        "🎥 اگر ویدئو داری بفرست.\n"
        "می‌توانی چند ویدئو بفرستی.\n"
        "اگر نداری «⏭ ویدئو ندارم» را بزن.",
        reply_markup=ReplyKeyboardMarkup(
            [["⏭ ویدئو ندارم"], ["❌ لغو"]],
            resize_keyboard=True,
        ),
    )
    return CAR_VIDEOS


async def car_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    videos = context.user_data["carpet_draft"]["videos"]
    video = update.message.video
    videos.append({
        "media_type": "video",
        "file_id": video.file_id,
        "unique_id": video.file_unique_id,
    })
    await update.message.reply_text(
        f"🎥 ویدئو دریافت شد. تعداد ویدئوها: {len(videos)}\n"
        "ویدئوی بعدی را بفرست یا «⏭ ویدئو ندارم» را بزن."
    )
    return CAR_VIDEOS


async def car_videos_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ لغو":
        return await cancel(update, context)
    if text != "⏭ ویدئو ندارم":
        await update.message.reply_text("ویدئو بفرست یا «⏭ ویدئو ندارم» را بزن.")
        return CAR_VIDEOS

    await update.message.reply_text("1/11\n🔢 کد فرش:", reply_markup=unknown_keyboard())
    return CAR_CODE


async def car_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["carpet_draft"]["code"] = optional_text(update.message.text)
    await update.message.reply_text("2/11\n🎨 طرح:", reply_markup=unknown_keyboard())
    return CAR_DESIGN


async def car_design(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["carpet_draft"]["design"] = optional_text(update.message.text)
    await update.message.reply_text("3/11\n🌈 رنگ:", reply_markup=unknown_keyboard())
    return CAR_COLOR


async def car_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["carpet_draft"]["color"] = optional_text(update.message.text)
    await update.message.reply_text("4/11\n🔢 شانه:", reply_markup=unknown_keyboard())
    return CAR_SHANEH


async def car_shaneh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    value = parse_int(update.message.text)
    if value is None and update.message.text not in {"⏭ نامشخص"}:
        await update.message.reply_text("عدد وارد کن؛ مثلاً 700 یا 1200.")
        return CAR_SHANEH
    context.user_data["carpet_draft"]["shaneh"] = value
    await update.message.reply_text("5/11\n🔢 تراکم:", reply_markup=unknown_keyboard())
    return CAR_DENSITY


async def car_density(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    value = parse_int(update.message.text)
    if value is None and update.message.text not in {"⏭ نامشخص"}:
        await update.message.reply_text("عدد وارد کن؛ مثلاً 2550 یا 3600.")
        return CAR_DENSITY
    context.user_data["carpet_draft"]["density"] = value
    await update.message.reply_text("6/11\n🧵 جنس نخ:", reply_markup=unknown_keyboard())
    return CAR_YARN


async def car_yarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["carpet_draft"]["yarn_material"] = optional_text(update.message.text)
    await update.message.reply_text("7/11\n📐 ابعاد / سایز:", reply_markup=unknown_keyboard())
    return CAR_SIZE


async def car_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["carpet_draft"]["size"] = optional_text(update.message.text)
    await update.message.reply_text(
        "8/11\n💰 قیمت خرید را به تومان وارد کن:",
        reply_markup=unknown_keyboard(),
    )
    return CAR_BUY


async def car_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    if update.message.text == "⏭ نامشخص":
        value = None
    else:
        value = parse_int(update.message.text)
        if value is None:
            await update.message.reply_text("فقط عدد وارد کن؛ مثلاً 18500000.")
            return CAR_BUY
    context.user_data["carpet_draft"]["purchase_price"] = value
    await update.message.reply_text(
        "9/11\n💵 قیمت پیشنهادی فروشنده برای فروش را به تومان وارد کن:",
        reply_markup=unknown_keyboard(),
    )
    return CAR_SELL


async def car_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    if update.message.text == "⏭ نامشخص":
        value = None
    else:
        value = parse_int(update.message.text)
        if value is None:
            await update.message.reply_text("فقط عدد وارد کن.")
            return CAR_SELL
    context.user_data["carpet_draft"]["suggested_sale_price"] = value
    await update.message.reply_text("10/11\n📦 موجودی / تعداد موجود:", reply_markup=unknown_keyboard())
    return CAR_STOCK


async def car_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    if update.message.text == "⏭ نامشخص":
        value = None
    else:
        value = parse_int(update.message.text)
        if value is None or value < 0:
            await update.message.reply_text("تعداد را به صورت عدد وارد کن.")
            return CAR_STOCK
    context.user_data["carpet_draft"]["stock"] = value
    await update.message.reply_text(
        "11/11\n📝 توضیحات:\n"
        "هر اطلاعات اضافه‌ای که درباره این فرش مهم است بنویس.\n"
        "اگر توضیحی نداری «⏭ نامشخص» را بزن.",
        reply_markup=unknown_keyboard(),
    )
    return CAR_DESC


async def car_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        return await cancel(update, context)
    context.user_data["carpet_draft"]["description"] = optional_text(update.message.text)
    d = context.user_data["carpet_draft"]

    summary = (
        "📋 بررسی نهایی فرش\n\n"
        f"🔢 کد: {d.get('code') or 'نامشخص'}\n"
        f"🎨 طرح: {d.get('design') or 'نامشخص'}\n"
        f"🌈 رنگ: {d.get('color') or 'نامشخص'}\n"
        f"🔢 شانه: {d.get('shaneh') or 'نامشخص'}\n"
        f"🔢 تراکم: {d.get('density') or 'نامشخص'}\n"
        f"🧵 نخ: {d.get('yarn_material') or 'نامشخص'}\n"
        f"📐 سایز: {d.get('size') or 'نامشخص'}\n"
        f"💰 خرید: {d.get('purchase_price') or 'نامشخص'} تومان\n"
        f"💵 فروش پیشنهادی: {d.get('suggested_sale_price') or 'نامشخص'} تومان\n"
        f"📦 موجودی: {d.get('stock') if d.get('stock') is not None else 'نامشخص'}\n"
        f"📷 عکس: {len(d['photos'])}\n"
        f"🎥 ویدئو: {len(d['videos'])}\n"
        f"📝 توضیحات: {d.get('description') or 'نامشخص'}\n\n"
        "ثبت نهایی؟"
    )
    await update.message.reply_text(
        summary,
        reply_markup=ReplyKeyboardMarkup(
            [["✅ ثبت نهایی"], ["✏️ اصلاح اطلاعات"], ["❌ لغو"]],
            resize_keyboard=True,
        ),
    )
    return CAR_CONFIRM


async def car_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ لغو":
        return await cancel(update, context)
    if text == "✏️ اصلاح اطلاعات":
        await update.message.reply_text(
            "برای نسخه اول، اصلاح اطلاعات متنی را در مرحله بعد فعال می‌کنیم.\n"
            "اگر مشکلی در اطلاعات می‌بینی، «❌ لغو» کن و دوباره ثبت کن."
        )
        return CAR_CONFIRM
    if text != "✅ ثبت نهایی":
        await update.message.reply_text("یکی از گزینه‌ها را انتخاب کن.")
        return CAR_CONFIRM

    d = context.user_data["carpet_draft"]

    with SessionLocal() as db:
        carpet = Carpet(
            supplier_id=d["supplier_id"],
            code=d.get("code"),
            design=d.get("design"),
            color=d.get("color"),
            shaneh=d.get("shaneh"),
            density=d.get("density"),
            yarn_material=d.get("yarn_material"),
            size=d.get("size"),
            purchase_price=d.get("purchase_price"),
            suggested_sale_price=d.get("suggested_sale_price"),
            stock=d.get("stock"),
            description=d.get("description"),
        )
        db.add(carpet)
        db.flush()

        for index, item in enumerate(d["photos"] + d["videos"], start=1):
            db.add(
                CarpetMedia(
                    carpet_id=carpet.id,
                    media_type=item["media_type"],
                    telegram_file_id=item["file_id"],
                    telegram_file_unique_id=item.get("unique_id"),
                    sort_order=index,
                )
            )

        db.commit()
        carpet_id = carpet.id

    context.user_data.pop("carpet_draft", None)

    await update.message.reply_text(
        f"✅ فرش با موفقیت ثبت شد.\n\n"
        f"شناسه فرش: {carpet_id}\n"
        f"📷 {len(d['photos'])} عکس\n"
        f"🎥 {len(d['videos'])} ویدئو",
        reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


async def list_carpets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    supplier = get_active_supplier(update.effective_user.id)
    if not supplier:
        await update.message.reply_text("ابتدا یک تأمین‌کننده فعال انتخاب کن.", reply_markup=main_keyboard())
        return

    with SessionLocal() as db:
        carpets = db.scalars(
            select(Carpet)
            .where(Carpet.supplier_id == supplier.id)
            .order_by(Carpet.id.desc())
            .limit(10)
        ).all()

    if not carpets:
        await update.message.reply_text("هنوز فرشی برای این تأمین‌کننده ثبت نشده.", reply_markup=main_keyboard())
        return

    lines = [f"📋 آخرین فرش‌های {supplier.name}:\n"]
    for c in carpets:
        lines.append(
            f"#{c.id} | {c.code or 'بدون کد'} | "
            f"{c.design or 'بدون طرح'} | "
            f"{c.size or 'بدون سایز'}"
        )

    await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard())


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as db:
        supplier_count = db.scalar(select(func.count()).select_from(Supplier)) or 0
        carpet_count = db.scalar(select(func.count()).select_from(Carpet)) or 0
        photo_count = db.scalar(
            select(func.count()).select_from(CarpetMedia).where(CarpetMedia.media_type == "photo")
        ) or 0
        video_count = db.scalar(
            select(func.count()).select_from(CarpetMedia).where(CarpetMedia.media_type == "video")
        ) or 0

    await update.message.reply_text(
        "📊 گزارش کلی\n\n"
        f"🏪 تأمین‌کننده‌ها: {supplier_count}\n"
        f"🧶 فرش‌ها: {carpet_count}\n"
        f"📷 عکس‌ها: {photo_count}\n"
        f"🎥 ویدئوها: {video_count}",
        reply_markup=main_keyboard(),
    )


async def menu_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "از منوی زیر انتخاب کن.",
        reply_markup=main_keyboard(),
    )


app = FastAPI(title="Carpet Supplier Bot")


ptb = (
    Application.builder()
    .token(BOT_TOKEN or "MISSING_TOKEN")
    .updater(None)
    .build()
)


supplier_conversation = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex(r"^🏪 تأمین‌کننده جدید$"), start_supplier)],
    states={
        SUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, sup_name)],
        SUP_PERSON: [MessageHandler(filters.TEXT & ~filters.COMMAND, sup_person)],
        SUP_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, sup_phone)],
        SUP_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, sup_city)],
        SUP_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, sup_address)],
        SUP_COOP: [MessageHandler(filters.TEXT & ~filters.COMMAND, sup_coop)],
        SUP_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, sup_confirm)],
    },
    fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex(r"^❌ لغو$"), cancel)],
)


carpet_conversation = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex(r"^➕ ثبت فرش$"), start_carpet)],
    states={
        CAR_PHOTOS: [
            MessageHandler(filters.PHOTO, car_photo),
            MessageHandler(filters.Regex(r"^(⏭ عکس‌ها تمام شد|⏭ بدون عکس|❌ لغو)$"), car_photos_done),
        ],
        CAR_VIDEOS: [
            MessageHandler(filters.VIDEO, car_video),
            MessageHandler(filters.Regex(r"^(⏭ ویدئو ندارم|❌ لغو)$"), car_videos_done),
        ],
        CAR_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_code)],
        CAR_DESIGN: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_design)],
        CAR_COLOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_color)],
        CAR_SHANEH: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_shaneh)],
        CAR_DENSITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_density)],
        CAR_YARN: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_yarn)],
        CAR_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_size)],
        CAR_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_buy)],
        CAR_SELL: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_sell)],
        CAR_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_stock)],
        CAR_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_desc)],
        CAR_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_confirm)],
    },
    fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex(r"^❌ لغو$"), cancel)],
)


ptb.add_handler(CommandHandler("start", start))
ptb.add_handler(supplier_conversation)
ptb.add_handler(carpet_conversation)
ptb.add_handler(MessageHandler(filters.Regex(r"^📋 فرش‌های ثبت‌شده$"), list_carpets))
ptb.add_handler(MessageHandler(filters.Regex(r"^📊 گزارش$"), report))
ptb.add_handler(MessageHandler(filters.TEXT, menu_fallback))


@app.get("/")
async def root():
    return {"status": "ok", "service": "carpet-supplier-bot"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/telegram")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    data = await request.json()
    update = Update.de_json(data=data, bot=ptb.bot)
    await ptb.update_queue.put(update)
    return JSONResponse({"ok": True})


@app.on_event("startup")
async def startup():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    init_db()
    await ptb.initialize()
    await ptb.start()

    if PUBLIC_URL:
        webhook_url = f"{PUBLIC_URL}/telegram"
        await ptb.bot.set_webhook(
            url=webhook_url,
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=Update.ALL_TYPES,
        )
        logger.info("Telegram webhook set: %s", webhook_url)
    else:
        logger.warning("PUBLIC_URL is not set; Telegram webhook was not configured.")


@app.on_event("shutdown")
async def shutdown():
    await ptb.stop()
    await ptb.shutdown()
