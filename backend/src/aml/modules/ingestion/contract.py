"""Records as the data contract defines them (version 0.1).

Each record is checked on its own, so one bad record never sinks a batch. ``check``
returns the validated record or the contract's error code with a detail that names the
field, never the rejected value (it may be personal data).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError

SCHEMA_VERSION = "0.1"

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Country = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Minor = Annotated[int, Field(ge=0, strict=True)]


def _with_offset(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PydanticCustomError("timestamp_offset", "must include a UTC offset")
    return value


# The contract rejects timestamps without an offset as TIMESTAMP_INVALID.
Timestamp = Annotated[datetime, AfterValidator(_with_offset)]


class CustomerType(StrEnum):
    INDIVIDUAL = "INDIVIDUAL"
    BUSINESS = "BUSINESS"


class KycStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class RiskRating(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CustomerStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    CLOSED = "CLOSED"
    RESTRICTED = "RESTRICTED"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    CLOSED = "CLOSED"
    FROZEN = "FROZEN"


class ControlType(StrEnum):
    OWNERSHIP = "OWNERSHIP"
    DIRECTOR = "DIRECTOR"
    SIGNATORY = "SIGNATORY"
    OTHER = "OTHER"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BeneficialOwner(Record):
    owner_id: Annotated[Text, StringConstraints(max_length=64)]
    full_name: Annotated[Text, StringConstraints(max_length=160)]
    date_of_birth: date | None = None
    nationality: Country | None = None
    ownership_percent: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] | None = None
    control_type: ControlType
    is_pep: bool = False


class Customer(Record):
    customer_id: Annotated[Text, StringConstraints(max_length=64)]
    customer_type: CustomerType
    full_name: Annotated[Text, StringConstraints(max_length=160)]
    date_of_birth: date | None = None
    nationality: Country | None = None
    occupation: Text | None = None
    industry: Text | None = None
    declared_income_min_minor: Minor | None = None
    declared_income_max_minor: Minor | None = None
    expected_monthly_credit_minor: Minor | None = None
    expected_monthly_credit_count: Minor | None = None
    state: Text | None = None
    country: Country | None = None
    onboarding_date: date
    kyc_status: KycStatus
    kyc_verified_at: Timestamp | None = None
    kyc_reference: Text | None = None
    risk_rating: RiskRating | None = None
    is_pep: bool = False
    segment: Text | None = None
    status: CustomerStatus
    registration_number: Text | None = None
    registration_date: date | None = None
    beneficial_owners: list[BeneficialOwner] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.customer_type is CustomerType.INDIVIDUAL and self.date_of_birth is None:
            raise ValueError("date_of_birth is required for individuals")
        if self.customer_type is CustomerType.INDIVIDUAL and self.beneficial_owners:
            raise ValueError("beneficial_owners apply to businesses only")
        if self.kyc_status is KycStatus.VERIFIED and self.kyc_verified_at is None:
            raise ValueError("kyc_verified_at is required when kyc_status is VERIFIED")
        low, high = self.declared_income_min_minor, self.declared_income_max_minor
        if low is not None and high is not None and high < low:
            raise ValueError("declared_income_max_minor is below declared_income_min_minor")
        owners = [owner.owner_id for owner in self.beneficial_owners]
        if len(owners) != len(set(owners)):
            raise ValueError("beneficial_owners repeat an owner_id")
        return self


class Account(Record):
    account_id: Annotated[Text, StringConstraints(max_length=64)]
    customer_id: Annotated[Text, StringConstraints(max_length=64)]
    product_type: Annotated[Text, StringConstraints(max_length=40)]
    currency: Currency
    opened_date: date
    status: AccountStatus
    branch_id: Text | None = None


class Direction(StrEnum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class Channel(StrEnum):
    CASH = "CASH"
    BRANCH = "BRANCH"
    ATM = "ATM"
    POS = "POS"
    USSD = "USSD"
    MOBILE_APP = "MOBILE_APP"
    WEB = "WEB"
    AGENT = "AGENT"
    TRANSFER = "TRANSFER"
    CARD = "CARD"
    OTHER = "OTHER"


class TransactionType(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRANSFER = "TRANSFER"
    BILL_PAYMENT = "BILL_PAYMENT"
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT"
    LOAN_REPAYMENT = "LOAN_REPAYMENT"
    FEE = "FEE"
    REVERSAL = "REVERSAL"
    INTEREST = "INTEREST"
    OTHER = "OTHER"


class TransactionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REVERSED = "REVERSED"
    PENDING = "PENDING"


class Transaction(Record):
    transaction_id: Annotated[Text, StringConstraints(max_length=64)]
    account_id: Annotated[Text, StringConstraints(max_length=64)]
    customer_id: Annotated[Text, StringConstraints(max_length=64)]
    timestamp: Timestamp
    value_date: date | None = None
    amount_minor: Annotated[int, Field(gt=0, strict=True)]
    currency: Currency
    direction: Direction
    channel: Channel
    transaction_type: TransactionType
    status: TransactionStatus
    counterparty_name: Annotated[Text, StringConstraints(max_length=120)] | None = None
    counterparty_account: Annotated[Text, StringConstraints(max_length=40)] | None = None
    counterparty_bank_code: Annotated[Text, StringConstraints(max_length=10)] | None = None
    narration: Annotated[Text, StringConstraints(max_length=255)] | None = None
    reference: Annotated[Text, StringConstraints(max_length=64)] | None = None
    reversal_of: Annotated[Text, StringConstraints(max_length=64)] | None = None
    location_id: Annotated[Text, StringConstraints(max_length=64)] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.reversal_of is not None and self.transaction_type is not TransactionType.REVERSAL:
            raise ValueError("reversal_of applies to transaction_type REVERSAL only")
        return self


class RecordRejectedError(Exception):
    """A record the contract does not allow, with the contract's error code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code, self.detail = code, detail


def check[R: Record](model: type[R], raw: Any) -> R:
    if not isinstance(raw, dict):
        raise RecordRejectedError("SCHEMA_INVALID", "record must be an object")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise _rejection(exc) from None


def _rejection(exc: ValidationError) -> RecordRejectedError:
    error = exc.errors()[0]
    field = ".".join(str(part) for part in error["loc"]) or "record"
    if error["type"] == "enum":
        allowed = error.get("ctx", {}).get("expected", "")
        return RecordRejectedError("ENUM_INVALID", f"{field} must be one of {allowed}")
    if error["type"] == "value_error":
        # Our own cross-field checks: the message names the fields.
        message = error.get("ctx", {}).get("error", error["msg"])
        return RecordRejectedError("SCHEMA_INVALID", str(message))
    if error["type"] == "timestamp_offset":
        return RecordRejectedError("TIMESTAMP_INVALID", f"{field} must include a UTC offset")
    if error["type"].startswith("datetime_"):
        return RecordRejectedError("TIMESTAMP_INVALID", f"{field}: {error['msg']}")
    if field.endswith("_minor") or field.endswith("_count"):
        least = "a positive" if field == "amount_minor" else "a non-negative"
        return RecordRejectedError("AMOUNT_INVALID", f"{field} must be {least} integer")
    return RecordRejectedError("SCHEMA_INVALID", f"{field}: {error['msg']}")
