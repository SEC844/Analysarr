from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AuthStatus(BaseModel):
    setup_required: bool
    authenticated: bool


class SetupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str
    # Code de l'application d'authentification ou code de secours : requis
    # seulement si la double authentification est activée.
    otp: Optional[str] = Field(default=None, max_length=32)


class CurrentUser(BaseModel):
    username: str
    two_factor_enabled: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ChangeUsernameRequest(BaseModel):
    username: str = Field(max_length=256)
    password: str


class PasswordConfirmation(BaseModel):
    password: str


class TwoFactorSetup(BaseModel):
    # Affichés une seule fois, pendant la configuration (QR code + clé à
    # saisir à la main) ; jamais renvoyés une fois la 2FA activée.
    secret: str
    otpauth_uri: str


class TwoFactorCode(BaseModel):
    code: str = Field(max_length=32)


class TwoFactorDisableRequest(BaseModel):
    password: str
    code: str = Field(max_length=32)


class RecoveryCodes(BaseModel):
    codes: list[str]


class LoginAttemptRead(BaseModel):
    """Entrée du journal de connexion (Réglages → Compte)."""

    created_at: datetime
    username: str
    ip: str
    success: bool
    reason: Optional[str] = None


class SecuritySettings(BaseModel):
    """Réglages de sécurité du compte (Réglages → Compte)."""

    trusted_proxies: str = ""


class SecuritySettingsWrite(BaseModel):
    trusted_proxies: str = Field(default="", max_length=200)
