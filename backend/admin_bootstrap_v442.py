from __future__ import annotations

import os
from sqlalchemy import select

from v4 import SessionLocal, User


def apply_admin_bootstrap() -> None:
    username = os.environ.get("CM_ADMIN_BOOTSTRAP_USERNAME", "").strip().lower()
    password_hash = os.environ.get("CM_ADMIN_BOOTSTRAP_PASSWORD_HASH", "").strip()
    if not username or not password_hash:
        return
    if not password_hash.startswith("pbkdf2_sha256$"):
        raise RuntimeError("CM_ADMIN_BOOTSTRAP_PASSWORD_HASH has an unsupported format")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            # Reuse the existing primary admin when possible so the client gets
            # one canonical admin account instead of accumulating duplicate admins.
            user = db.scalar(select(User).where(User.role == "admin").order_by(User.id.asc()))

        if user is None:
            user = User(
                username=username,
                full_name="Admin",
                phone="",
                password_hash=password_hash,
                role="admin",
                active=True,
            )
            db.add(user)
        else:
            user.username = username
            user.password_hash = password_hash
            user.role = "admin"
            user.active = True
            if not (user.full_name or "").strip():
                user.full_name = "Admin"

        db.commit()


apply_admin_bootstrap()
