from app.services.session_store import SessionStore


class AuthService:
    def __init__(self, session_store: SessionStore) -> None:
        self.session_store: SessionStore = session_store

    def login(self, username: str, password: str) -> str | None:
        if username == "admin" and password == "admin":
            return self.session_store.create(user_id="admin", username=username)
        return None
