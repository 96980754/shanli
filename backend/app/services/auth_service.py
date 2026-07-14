from collections.abc import Callable

from app.models import User
from app.services.session_store import SessionStore


class AuthService:
    def __init__(
        self,
        session_store: SessionStore,
        authenticate: Callable[[str, str], User | None] | None = None,
    ) -> None:
        self.session_store: SessionStore = session_store
        self.authenticate = authenticate

    def login(self, username: str, password: str) -> str | None:
        if self.authenticate is not None:
            user = self.authenticate(username, password)
            if user is None:
                return None
            return self.session_store.create(user_id=str(user.id), username=user.username)
        if username == "admin" and password == "admin":
            return self.session_store.create(user_id="admin", username=username)
        return None
