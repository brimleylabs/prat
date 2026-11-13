"""Sales tax."""

from tinyshop.config import TAX_RATE_BASIS_POINTS
from tinyshop.money import basis_points_of


def rate_for(region: str) -> int:
    return TAX_RATE_BASIS_POINTS.get(region, TAX_RATE_BASIS_POINTS["DEFAULT"])


def tax_cents(taxable_cents: int, region: str) -> int:
    return basis_points_of(taxable_cents, rate_for(region))
