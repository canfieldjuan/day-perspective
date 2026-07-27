/**
 * Where a published model card can actually be read.
 *
 * The payload carries the card's identifier and a repository-relative path.
 * That path is enough to find the file in a checkout and useless to a
 * reader on the web, so the identifier is resolved here to a served URL.
 * "No comparison ships without a card" is only true if the card is
 * reachable from the page that relies on it.
 */
const MODEL_CARD_BASE =
  "https://github.com/canfieldjuan/day-perspective/blob/main/docs/MODEL_CARDS";

/** Identifiers come from published payloads, so they are constrained rather
 * than trusted: a card id is a slug, and anything else gets no link. */
export function modelCardUrl(identifier: string): string | undefined {
  return /^[a-z0-9][a-z0-9-]*$/.test(identifier)
    ? `${MODEL_CARD_BASE}/${identifier}.md`
    : undefined;
}
