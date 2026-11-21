"""Shop settings."""

# Sales tax per region, in basis points (825 is 8.25%).
TAX_RATE_BASIS_POINTS = {"US-TX": 825, "US-OR": 0, "DEFAULT": 800}

# Orders are delivered this many business days (Monday to Friday) after they are placed.
DELIVERY_BUSINESS_DAYS = 2

# Coupon code: (kind, amount). Percent coupons take a share of the subtotal; fixed ones cents.
COUPONS = {"SAVE10": ("percent", 10), "FIVEOFF": ("fixed", 500)}
