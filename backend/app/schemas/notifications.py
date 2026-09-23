from typing import Literal

from pydantic import BaseModel, Field

ChannelKind = Literal["discord", "ntfy", "gotify"]


class ChannelRead(BaseModel):
    id: int
    kind: ChannelKind
    name: str
    enabled: bool
    events: list[str]
    # Adresse renvoyée uniquement pour Gotify : celle de Discord (webhook) et
    # de ntfy (nom du sujet) sont des secrets.
    url: str | None = None
    url_set: bool
    token_set: bool


class ChannelWrite(BaseModel):
    kind: ChannelKind
    name: str = Field(max_length=40)
    enabled: bool = True
    events: list[str] = []
    # À la création : obligatoire. À la mise à jour : vide = valeur conservée.
    url: str | None = Field(default=None, max_length=2048)
    token: str | None = Field(default=None, max_length=256)


class ChannelTestResult(BaseModel):
    ok: bool
    # Message d'erreur réduit (code HTTP ou type d'erreur), jamais l'URL.
    error: str | None = None
