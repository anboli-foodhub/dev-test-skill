# Test design catalogue

Pick what applies to the change. Not every item fits every ticket; skipping one is fine, skipping a whole section needs a reason in the matrix.

## Inputs and data

- Empty string, whitespace only, leading/trailing spaces, `null`/missing field, field sent twice
- Zero, negative, decimal where an integer is expected, very large number, `1e5`, `NaN`
- Exactly at the length limit, one over, one under; DB column length vs UI `maxlength`
- Special characters: `' " < > & ; \ / % _ #`, SQL wildcards, HTML tags, `<script>`
- Unicode: accents (é), non-Latin scripts, emoji, right-to-left text, pasted text with zero-width or smart quotes
- Dates: today, past, future, 29 Feb, month end, DST change days, midnight, times in other timezones, store vs server vs browser timezone
- Money: rounding to 2 dp, 0.005 cases, currency symbol and format per country, negative/refund amounts, VAT/tax inclusive vs exclusive
- Phone, email, postcode formats per supported country
- IDs that don't exist, IDs belonging to another store/customer/tenant
- Lists: zero items, one item, exactly a page size, page size + 1, very many items

## Users and state

- Each role that can reach the screen, and one that should not
- Logged out, session expired mid-flow, two tabs with different users
- Feature flag / store setting on, off, and missing (old rows without the column set)
- Records created before the change (old data) as well as new ones
- Partially filled or previously failed records

## Flow

- Double click / double submit, refresh after submit, browser back then resubmit
- Two users editing the same record at once
- Navigating away mid-flow and returning
- Slow or failing downstream call (timeout, 500, malformed response) - is the error shown, logged, and is state left consistent?
- Retry after a failure does not duplicate data

## UI

- Validation messages: shown, correct wording, cleared once fixed
- Loading and disabled states while a request is in flight
- Mobile width and a narrow desktop width if the screen is used on both
- No console errors, no failed network calls on page load
- Values shown match what is stored (formatting, rounding, timezone)

## Backward compatibility

Check each against the diff; every one that applies becomes a BC risk and a test case.

- **API contract**: field renamed, removed, type changed (string ↔ int, null ↔ empty), new required request field, changed status codes or error format. Who calls it - web, mobile apps (old versions stay installed), other services, partners?
- **Database**: new NOT NULL column without default, changed column type or length, renamed/dropped column or table, new unique index on data that may hold duplicates, migration that locks a large table, existing rows that don't satisfy the new rule.
- **Defaults**: a setting or behaviour that used to be on is now off (or the reverse) for existing stores/users.
- **Shared code**: the changed function, class, include or stored procedure is used by other screens, crons, queues or repos.
- **Multiple live copies**: the same feature exists in more than one file or version folder - is the change in every copy that serves traffic, and is it intentionally absent in the others?
- **Config and environment**: new env var or config key - what happens when it is missing?
- **Caches and stored payloads**: cached values, queued jobs or serialized data written by the old code and read by the new code (and the reverse during deploy).
- **Rollback**: if this release is rolled back, does the old code still work with data the new code wrote?

## Security quick checks

- Every new endpoint or action checks login and role
- Another store's/customer's id in the request is rejected
- Input reaches SQL only through bound parameters; output is escaped where it is rendered
- No secrets, tokens or personal data in responses, logs or error messages

## Priority guide

- **P0** - the main purpose of the ticket, money, data loss, security, login
- **P1** - each AC's main path and its most likely failure
- **P2** - corner cases and regression of neighbouring features
- **P3** - cosmetic, wording, layout
