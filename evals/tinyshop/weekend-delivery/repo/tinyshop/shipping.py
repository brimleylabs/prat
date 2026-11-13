"""Delivery."""

from datetime import date

from tinyshop.calendar_util import add_business_days
from tinyshop.config import DELIVERY_BUSINESS_DAYS


def delivery_estimate(placed_on: date) -> date:
    return add_business_days(placed_on, DELIVERY_BUSINESS_DAYS)
