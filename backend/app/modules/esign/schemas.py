import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class SignerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(pattern=EMAIL, max_length=200)
    routing_order: int = Field(default=1, ge=1, le=20, description="Signers with a lower order sign first")
    user_id: uuid.UUID | None = Field(default=None, description="A firm member; found by e-mail when omitted")

    @field_validator("name", "email")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class SignatureSend(BaseModel):
    signers: list[SignerIn] = Field(min_length=1, max_length=10)
    subject: str | None = Field(default=None, max_length=100, description="E-mail subject; a default is used")
    message: str | None = Field(default=None, max_length=2000, description="Shown in the signing e-mail")
    confirm_not_excluded: bool = Field(
        default=False,
        description="The sender confirms the document is not one Indian law excludes from e-signature "
        "(negotiable instruments other than cheques, powers of attorney, trust deeds, wills, contracts "
        "for the sale or conveyance of immovable property)",
    )


class SignatureVoid(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class FakeAct(BaseModel):
    action: Literal["deliver", "sign_next", "sign_all", "decline"]
    reason: str | None = Field(default=None, max_length=200)


class ESignStatus(BaseModel):
    backend: Literal["none", "fake", "docusign"]
    ready: bool = Field(description="True when drafts can be sent for signature now")
    environment: Literal["test", "sandbox", "production"] | None = None
    account_name: str | None = None
    detail: str | None = Field(default=None, description="What to fix when not ready")
    consent_url: str | None = Field(
        default=None, description="DocuSign consent link, when consent is missing"
    )
    callbacks: bool = Field(default=False, description="Status arrives by DocuSign Connect, not only polling")
