# Database structure — Carpet Supplier Bot

## suppliers
Stores supplier/shop/factory information.

- id
- name
- contact_person
- phone
- city
- address
- cooperation
- created_at
- is_active

## carpets
Stores each carpet and links it to one supplier.

- id
- supplier_id -> suppliers.id
- code
- design
- color
- shaneh
- density
- yarn_material
- size
- purchase_price
- suggested_sale_price
- stock
- description
- created_at

## carpet_media
Stores media metadata for each carpet. The actual media remains in Telegram initially; Telegram file_id and file_unique_id are stored so the bot can retrieve/download media later.

- id
- carpet_id -> carpets.id
- media_type: photo | video
- telegram_file_id
- telegram_file_unique_id
- sort_order
- created_at

## user_sessions
Stores the currently active supplier for each Telegram user so the active supplier survives bot restarts.

- id
- telegram_user_id
- active_supplier_id -> suppliers.id
- updated_at

## Architecture

Telegram -> FastAPI webhook -> python-telegram-bot -> SQLAlchemy -> PostgreSQL

For local development, if DATABASE_URL is not set, SQLite is used automatically. In production, set DATABASE_URL to PostgreSQL.
