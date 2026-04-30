"""
seed.py — Populate the database with initial data.

Run after `alembic upgrade head`.

Creates:
  • 1 admin account (skipped if a user with that email already exists)
  • 10 sample products (skipped individually if a product with the same name exists)

Usage:
    python seed.py
"""

import asyncio
import os
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Bootstrap the app config so DATABASE_URL is available.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.user import User  # noqa: E402

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@agapi.local")
ADMIN_USERNAME = os.getenv("SEED_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "Admin@1234!")

SEED_PRODUCTS = [
    {
        "name": "Wireless Noise-Cancelling Headphones",
        "description": "Over-ear headphones with 40-hour battery life and active noise cancellation.",
        "price": Decimal("149.99"),
        "stock": 200,
    },
    {
        "name": "Mechanical Keyboard",
        "description": "Tenkeyless mechanical keyboard with Cherry MX Red switches and RGB backlight.",
        "price": Decimal("89.99"),
        "stock": 150,
    },
    {
        "name": "USB-C Docking Station",
        "description": "12-in-1 hub with dual HDMI, 4× USB-A, USB-C PD 100W, SD card reader, and Ethernet.",
        "price": Decimal("69.99"),
        "stock": 300,
    },
    {
        "name": "4K Webcam",
        "description": "Ultra-HD webcam with built-in privacy shutter, autofocus, and stereo microphone.",
        "price": Decimal("119.99"),
        "stock": 120,
    },
    {
        "name": "Ergonomic Office Chair",
        "description": "Adjustable lumbar support, breathable mesh back, and 5-year warranty.",
        "price": Decimal("349.00"),
        "stock": 60,
    },
    {
        "name": "Smart LED Desk Lamp",
        "description": "Touch-controlled desk lamp with USB-A charging port and 5 colour temperatures.",
        "price": Decimal("39.99"),
        "stock": 500,
    },
    {
        "name": "Portable SSD 1TB",
        "description": "NVMe portable SSD with read speeds up to 1 050 MB/s and shock-resistant casing.",
        "price": Decimal("109.99"),
        "stock": 180,
    },
    {
        "name": "Wireless Charging Pad",
        "description": "Qi-certified 15W fast-charging pad compatible with all Qi-enabled devices.",
        "price": Decimal("24.99"),
        "stock": 400,
    },
    {
        "name": "Bluetooth Mechanical Numpad",
        "description": "Compact Bluetooth numpad with rechargeable battery and hot-swappable switches.",
        "price": Decimal("44.99"),
        "stock": 250,
    },
    {
        "name": "27-inch 4K Monitor",
        "description": "IPS panel, 144 Hz refresh rate, 1ms GtG, USB-C 65W PD, and HDR400 support.",
        "price": Decimal("599.00"),
        "stock": 80,
    },
]


# ---------------------------------------------------------------------------
# Core seed logic
# ---------------------------------------------------------------------------


async def seed_admin(session: AsyncSession) -> None:
    result = await session.execute(select(User).where(User.email == ADMIN_EMAIL))
    existing = result.scalar_one_or_none()
    if existing:
        print(f"  [SKIP] Admin account already exists: {ADMIN_EMAIL}")
        return

    admin = User(
        email=ADMIN_EMAIL,
        username=ADMIN_USERNAME,
        hashed_password=hash_password(ADMIN_PASSWORD),
        is_active=True,
        is_admin=True,
        is_email_verified=True,
    )
    session.add(admin)
    print(f"  [CREATE] Admin account: {ADMIN_EMAIL} / username: {ADMIN_USERNAME}")


async def seed_products(session: AsyncSession) -> None:
    for data in SEED_PRODUCTS:
        result = await session.execute(
            select(Product).where(Product.name == data["name"])
        )
        existing = result.scalar_one_or_none()
        if existing:
            print(f"  [SKIP] Product already exists: {data['name']}")
            continue

        product = Product(
            name=data["name"],
            description=data["description"],
            price=data["price"],
            stock=data["stock"],
        )
        session.add(product)
        print(f"  [CREATE] Product: {data['name']}  (stock={data['stock']}, price={data['price']})")


async def run() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    print("\n=== Seeding database ===\n")
    async with async_session() as session:
        async with session.begin():
            await seed_admin(session)
            await seed_products(session)

    await engine.dispose()
    print("\n=== Seed complete ===\n")


if __name__ == "__main__":
    asyncio.run(run())
