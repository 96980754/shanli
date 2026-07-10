from typing import TypedDict
from uuid import uuid4


class SessionData(TypedDict):
    user_id: str
    username: str


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}

    def create(self, user_id: str, username: str) -> str:
        token = uuid4().hex
        self._sessions[token] = {"user_id": user_id, "username": username}
        return token

    def get(self, token: str) -> SessionData | None:
        return self._sessions.get(token)
