from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthContext:
    subject: str
    role: str
    source: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
