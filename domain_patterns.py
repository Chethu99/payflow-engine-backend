from abc import ABC, abstractmethod
from decimal import Decimal
import uuid


# =========================================================
# 1. STATE PATTERN: INVOICE LIFECYCLE STATE MACHINE
# =========================================================
class IllegalStateTransitionError(Exception):
    """Raised when an action is invalid for the invoice's current status."""
    pass


class InvoiceState(ABC):
    """Base interface defining allowed actions in any invoice state."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    def submit(self) -> "InvoiceState":
        raise IllegalStateTransitionError(f"Cannot submit an invoice in '{self.name}' state.")

    def approve(self, actor_role: str) -> "InvoiceState":
        raise IllegalStateTransitionError(f"Cannot approve an invoice in '{self.name}' state.")

    def reject(self, actor_role: str) -> "InvoiceState":
        raise IllegalStateTransitionError(f"Cannot reject an invoice in '{self.name}' state.")

    def pay(self, actor_role: str) -> "InvoiceState":
        raise IllegalStateTransitionError(f"Cannot disburse payment in '{self.name}' state.")


class DraftState(InvoiceState):
    name = "DRAFT"

    def submit(self) -> InvoiceState:
        return PendingApprovalState()


class PendingApprovalState(InvoiceState):
    name = "PENDING_APPROVAL"

    def approve(self, actor_role: str) -> InvoiceState:
        if actor_role not in ("MANAGER", "FINANCE"):
            raise PermissionError(f"Role '{actor_role}' is not authorized to approve invoices.")
        return ApprovedState()

    def reject(self, actor_role: str) -> InvoiceState:
        if actor_role not in ("MANAGER", "FINANCE"):
            raise PermissionError(f"Role '{actor_role}' is not authorized to reject invoices.")
        return RejectedState()


class ApprovedState(InvoiceState):
    name = "APPROVED"

    def pay(self, actor_role: str) -> InvoiceState:
        if actor_role != "FINANCE":
            raise PermissionError(f"Role '{actor_role}' cannot disburse funds. FINANCE role required.")
        return PaidState()


class RejectedState(InvoiceState):
    name = "REJECTED"


class PaidState(InvoiceState):
    name = "PAID"


class InvoiceStateMachine:
    """Hydrates the current state object from a DB status string."""

    _STATE_MAP = {
        "DRAFT": DraftState,
        "PENDING_APPROVAL": PendingApprovalState,
        "APPROVED": ApprovedState,
        "REJECTED": RejectedState,
        "PAID": PaidState,
    }

    @classmethod
    def from_status(cls, status_str: str) -> InvoiceState:
        state_cls = cls._STATE_MAP.get(status_str.upper())
        if not state_cls:
            raise ValueError(f"Unknown invoice status: {status_str}")
        return state_cls()


# =========================================================
# 2. STRATEGY PATTERN: PLUGGABLE PAYMENT RAILS
# =========================================================
class PaymentGateway(ABC):
    """Abstract Strategy interface for external payout providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @abstractmethod
    def execute_payout(self, invoice_number: str, vendor_name: str, amount: Decimal) -> dict:
        pass


class RazorpayXStrategy(PaymentGateway):
    provider_name = "RAZORPAYX"

    def execute_payout(self, invoice_number: str, vendor_name: str, amount: Decimal) -> dict:
        utr = f"UTR{uuid.uuid4().hex[:10].upper()}"
        return {
            "provider": self.provider_name,
            "transaction_ref": utr,
            "settled_amount": str(amount),
            "rail": "IMPS_INSTANT",
            "status": "SETTLED",
        }


class StripeConnectStrategy(PaymentGateway):
    provider_name = "STRIPE"

    def execute_payout(self, invoice_number: str, vendor_name: str, amount: Decimal) -> dict:
        transfer_id = f"tr_{uuid.uuid4().hex[:14]}"
        return {
            "provider": self.provider_name,
            "transaction_ref": transfer_id,
            "settled_amount": str(amount),
            "rail": "SWIFT_WIRE",
            "status": "SETTLED",
        }


class InternalWireStrategy(PaymentGateway):
    provider_name = "WIRE"

    def execute_payout(self, invoice_number: str, vendor_name: str, amount: Decimal) -> dict:
        ref = f"WIRE-{uuid.uuid4().hex[:8].upper()}"
        return {
            "provider": self.provider_name,
            "transaction_ref": ref,
            "settled_amount": str(amount),
            "rail": "DOMESTIC_NEFT",
            "status": "SETTLED",
        }


# =========================================================
# 3. FACTORY PATTERN: RUNTIME GATEWAY SELECTION
# =========================================================
class PaymentGatewayFactory:
    """Instantiates the requested PaymentGateway strategy cleanly."""

    _GATEWAYS = {
        "RAZORPAYX": RazorpayXStrategy,
        "STRIPE": StripeConnectStrategy,
        "WIRE": InternalWireStrategy,
    }

    @classmethod
    def get_gateway(cls, rail_code: str) -> PaymentGateway:
        gateway_cls = cls._GATEWAYS.get(rail_code.upper())
        if not gateway_cls:
            supported = ", ".join(cls._GATEWAYS.keys())
            raise ValueError(f"Unsupported payment rail '{rail_code}'. Choose from: {supported}")
        return gateway_cls()