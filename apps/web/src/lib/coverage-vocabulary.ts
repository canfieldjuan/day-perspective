/**
 * The words the interface is allowed to use about coverage, and the field
 * that licenses each one.
 *
 * Four review rounds on this slice each found copy asserting a property the
 * index does not carry — "reviewed" with no review field, "demographic" for
 * armed-conflict counts, "same year" inferred from a distance band, and
 * then "evidence-backed" used as though it distinguished anything. Defining
 * the vocabulary in one place, next to its evidence, is what stops the next
 * adjective being invented at a call site.
 */

/**
 * A date whose publication tier is richer than `context_only`.
 *
 * Licensed by: `nearest_enriched_before` / `nearest_enriched_after`, which
 * the API derives from `publication_tier != context_only`.
 *
 * NOT "evidence-backed": every published date is evidence-backed, including
 * the 27,758 context-only ones, so that word distinguishes nothing and
 * denying such dates exist would be false.
 *
 * NOT "reviewed": coverage carries no review-status field. It was removed
 * from the index precisely because it claimed review the data could not
 * prove (issue #45).
 */
export const ENRICHED_TERM = "enriched";

/**
 * A statement about something that happened on the date itself.
 *
 * Licensed by: `has_recorded_event`, and per-destination by comparing
 * against `nearest_recorded_event_*`. A `partially_enriched` date is
 * enriched without holding one.
 */
export const RECORDED_EVENT_TERM = "recorded event";

/**
 * The annual daily equivalents.
 *
 * Licensed by: a non-zero `typical_day_in_this_year` count. NOT by
 * `wider_historical_context`, which the product contract defines as any
 * surrounding-period condition and where UCDP publishes armed-conflict
 * counts.
 */
export const DEMOGRAPHIC_TERM = "demographic context";

/**
 * Surrounding-period conditions of any kind.
 *
 * Licensed by: a non-zero `wider_historical_context` count.
 */
export const PERIOD_TERM = "period context";
