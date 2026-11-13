# tinyshop cases

A small shop backend written for these cases: catalog, cart, coupons, tax, checkout, refunds,
delivery dates and emails, in 23 files. Each case is the whole codebase with one bug, described
by what a customer saw:

| Case | Symptom given to the agent | Where the bug is |
| --- | --- | --- |
| weekend-delivery | confirmation emails promise delivery on a Saturday | the business-day helper, three calls away from the email |
| partial-refund | returning one product of a coupon order pays back too much | the discount share in refunds |

The hidden check (`hidden/hidden_check.py`) checks the behaviour the card asks for, including
cases the visible tests do not cover.
