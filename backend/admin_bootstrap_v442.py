from __future__ import annotations

import os
from sqlalchemy import select

from v4 import SessionLocal, User, hash_password


def apply_admin_bootstrap() -> None:
    username = os.environ.get("CM_ADMIN_BOOTSTRAP_USERNAME", "").strip().lower()
    password = os.environ.get("CM_ADMIN_BOOTSTRAP_PASSWORD", "")
    if not username or not password:
        return
    if len(password) < 8:
        raise RuntimeError("CM_ADMIN_BOOTSTRAP_PASSWORD must be at least 8 characters")

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
                password_hash=hash_password(password),
                role="admin",
                active=True,
            )
            db.add(user)
        else:
            user.username = username
            user.password_hash = hash_password(password)
            user.role = "admin"
            user.active = True
            if not (user.full_name or "").strip():
                user.full_name = "Admin"

        db.commit()


apply_admin_bootstrap()
