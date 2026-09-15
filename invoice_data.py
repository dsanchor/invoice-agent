import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Annotated

from agent_framework import tool
from pydantic import Field


@dataclass(frozen=True)
class Product:
    name: str
    quantity: int
    price_per_unit: float

    def to_dict(self) -> dict[str, str | int | float]:
        return {
            **asdict(self),
            "total_price": self.quantity * self.price_per_unit,
        }


@dataclass(frozen=True)
class Invoice:
    transaction_id: str
    invoice_id: str
    company_name: str
    invoice_date: date
    products: tuple[Product, ...]

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "invoice_id": self.invoice_id,
            "company_name": self.company_name,
            "invoice_date": self.invoice_date.isoformat(),
            "products": [product.to_dict() for product in self.products],
            "total_invoice_price": sum(
                product.quantity * product.price_per_unit for product in self.products
            ),
        }


INVOICES = (
    Invoice(
        "TICKET-XYZ987",
        "INV789",
        "Contoso",
        date(2026, 8, 12),
        (
            Product("T-Shirts", 150, 10.00),
            Product("Hats", 200, 15.00),
            Product("Glasses", 300, 5.00),
        ),
    ),
    Invoice(
        "TICKET-XYZ333",
        "INV333",
        "Contoso",
        date(2026, 8, 28),
        (
            Product("T-Shirts", 400, 11.00),
            Product("Hats", 600, 15.00),
            Product("Glasses", 700, 5.00),
        ),
    ),
    Invoice(
        "TICKET-XYZ111",
        "INV111",
        "XStore",
        date(2026, 9, 2),
        (
            Product("T-Shirts", 2500, 12.00),
            Product("Hats", 1500, 8.00),
            Product("Glasses", 200, 20.00),
        ),
    ),
    Invoice(
        "TICKET-XYZ222",
        "INV222",
        "Cymbal Direct",
        date(2026, 9, 8),
        (
            Product("T-Shirts", 1200, 14.00),
            Product("Hats", 800, 7.00),
            Product("Glasses", 500, 25.00),
        ),
    ),
)


def _serialize(invoices: list[Invoice]) -> str:
    return json.dumps([invoice.to_dict() for invoice in invoices], indent=2)


@tool(approval_mode="never_require")
def query_invoices(
    company_name: Annotated[
        str, Field(description="Company name used to filter invoices.")
    ],
    start_date: Annotated[
        str | None, Field(description="Optional start date in YYYY-MM-DD format.")
    ] = None,
    end_date: Annotated[
        str | None, Field(description="Optional end date in YYYY-MM-DD format.")
    ] = None,
) -> str:
    """Retrieve invoices for a company, optionally within an inclusive date range."""
    normalized_company = company_name.casefold()
    results = [
        invoice
        for invoice in INVOICES
        if invoice.company_name.casefold() == normalized_company
    ]

    if start_date:
        lower_bound = date.fromisoformat(start_date)
        results = [invoice for invoice in results if invoice.invoice_date >= lower_bound]
    if end_date:
        upper_bound = date.fromisoformat(end_date)
        results = [invoice for invoice in results if invoice.invoice_date <= upper_bound]

    return _serialize(results)


@tool(approval_mode="never_require")
def query_by_transaction_id(
    transaction_id: Annotated[
        str, Field(description="Transaction ID to look up, for example TICKET-XYZ987.")
    ],
) -> str:
    """Retrieve an invoice by transaction ID."""
    normalized_id = transaction_id.casefold()
    return _serialize(
        [
            invoice
            for invoice in INVOICES
            if invoice.transaction_id.casefold() == normalized_id
        ]
    )


@tool(approval_mode="never_require")
def query_by_invoice_id(
    invoice_id: Annotated[
        str, Field(description="Invoice ID to look up, for example INV789.")
    ],
) -> str:
    """Retrieve an invoice by invoice ID."""
    normalized_id = invoice_id.casefold()
    return _serialize(
        [
            invoice
            for invoice in INVOICES
            if invoice.invoice_id.casefold() == normalized_id
        ]
    )