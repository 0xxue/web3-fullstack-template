from pydantic import BaseModel, Field, model_validator
from datetime import datetime


class OwnerInput(BaseModel):
    """签名人输入：admin_id 或 address 二选一"""
    admin_id: int | None = None
    address: str | None = None

    @model_validator(mode="after")
    def check_one_of(self):
        if not self.admin_id and not self.address:
            raise ValueError("admin_id 和 address 必须填一个")
        if self.admin_id and self.address:
            raise ValueError("admin_id 和 address 只能填一个")
        return self


class MultisigWalletCreate(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    type: str = Field(..., pattern="^(collection|payout)$")
    label: str | None = Field(None, max_length=100)
    owners: list[OwnerInput] = Field(..., min_length=2)
    threshold: int = Field(..., ge=1)
    gas_wallet_id: int | None = None  # BSC 用：指定 gas 钱包


class MultisigWalletImport(BaseModel):
    chain: str = Field(..., pattern="^(BSC|TRON)$")
    type: str = Field(..., pattern="^(collection|payout)$")
    address: str = Field(..., max_length=128)
    label: str | None = Field(None, max_length=100)


class MultisigWalletOut(BaseModel):
    id: int
    chain: str
    type: str
    address: str | None = None
    label: str | None = None
    is_multisig: bool = True
    owners: list[str] | None = None
    threshold: int | None = None
    multisig_status: str | None = None
    deployment_tx: str | None = None
    derive_index: int | None = None
    relay_wallet_id: int | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SignerInfo(BaseModel):
    admin_id: int
    username: str
    address: str | None = None


class VerifyResult(BaseModel):
    owners: list[str]
    threshold: int
