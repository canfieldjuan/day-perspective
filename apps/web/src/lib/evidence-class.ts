import type {
  DayProfileSectionKey,
  ProfileStatement
} from "@day-perspective/contracts";

export type EvidenceClassKey =
  | "recorded"
  | "daily-average"
  | "date-modeled"
  | "period-context"
  | "curated"
  | "comparison"
  | "archive-note"
  | "unavailable"
  | "unclassified";

export interface EvidenceClass {
  key: EvidenceClassKey;
  label: string;
  /** Canonical caveat per UI_UX_CONTRACT C-4.2; "{year}" is replaced by the renderer. */
  caveat: string | null;
}

const CLASSES: Record<EvidenceClassKey, EvidenceClass> = {
  recorded: { key: "recorded", label: "Recorded on this date", caveat: null },
  "daily-average": {
    key: "daily-average",
    label: "Annual daily average",
    caveat: "Average across {year} — not a count for this date."
  },
  "date-modeled": {
    key: "date-modeled",
    label: "Modeled for this date",
    caveat: "Modeled estimate for this date, not a recorded observation."
  },
  "period-context": {
    key: "period-context",
    label: "Period context",
    caveat: "Describes the surrounding period, not this date specifically."
  },
  curated: { key: "curated", label: "Curated claim", caveat: null },
  comparison: { key: "comparison", label: "App-derived comparison", caveat: null },
  "archive-note": { key: "archive-note", label: "About this evidence", caveat: null },
  unavailable: { key: "unavailable", label: "Not available", caveat: null },
  unclassified: {
    key: "unclassified",
    label: "Evidence class unstated",
    caveat: null
  }
};

/**
 * Section defaults per the UI_UX_CONTRACT C-4 table. wonder_and_progress is
 * deliberately absent (C-4.5): it classifies by markers alone and falls back
 * to `unclassified` (C-4.6) rather than inheriting a stronger default.
 */
const SECTION_DEFAULTS: Partial<Record<DayProfileSectionKey, EvidenceClassKey>> = {
  recorded_on_this_date: "recorded",
  typical_day_in_this_year: "daily-average",
  wider_historical_context: "period-context",
  curated_claims: "curated",
  derived_comparisons: "comparison",
  evidence_notes: "archive-note"
};

function detailString(
  details: ProfileStatement["details"],
  key: string
): string | undefined {
  if (!details || typeof details !== "object" || Array.isArray(details)) {
    return undefined;
  }
  const value = (details as Record<string, unknown>)[key];
  return typeof value === "string" ? value : undefined;
}

/**
 * Derive the single base evidence class for a statement
 * (UI_UX_CONTRACT C-4). Pure and never-throwing: the discriminant is the
 * validated presence of `resolved_claim` vs `derived_value` (the payload
 * validator guarantees at least one when provenance exists); untyped
 * `details` markers only refine, and unknown markers degrade to the
 * section default, never to a stronger claim.
 */
export function deriveEvidenceClass(
  sectionKey: DayProfileSectionKey,
  statementValue: ProfileStatement
): EvidenceClass {
  const provenance = statementValue.provenance;
  if (!provenance) {
    return CLASSES.unclassified;
  }

  const hasResolved = provenance.resolved_claim !== undefined;
  const hasDerived = provenance.derived_value !== undefined;
  const dataStatus = detailString(statementValue.details, "data_status");
  const temporalAssignment = detailString(
    statementValue.details,
    "temporal_assignment"
  );

  if (dataStatus === "missing") {
    return CLASSES.unavailable;
  }
  if (hasDerived && provenance.derived_value?.value === null) {
    return CLASSES.unavailable;
  }
  if (hasDerived && temporalAssignment === "modeled_period_allocation") {
    return CLASSES["date-modeled"];
  }
  if (hasDerived && temporalAssignment === "uniform_period_allocation") {
    return CLASSES["daily-average"];
  }
  const sectionDefault = SECTION_DEFAULTS[sectionKey];

  // Authorship outranks temporal framing. An app-derived comparison is also
  // period context — it describes a year — but a reader who is told only
  // "Period context" cannot tell that the application made this claim
  // rather than a source. That distinction is the whole reason the class
  // exists, so the comparison section is settled before the temporal
  // branches below can claim it.
  if (sectionDefault === "comparison" && hasDerived) {
    const comparability = detailString(
      statementValue.details,
      "comparability_status"
    );
    return comparability
      ? { ...CLASSES.comparison, caveat: "Comparability: " + comparability + "." }
      : CLASSES.comparison;
  }

  if (
    temporalAssignment === "period_context" ||
    temporalAssignment === "editorial_context"
  ) {
    return CLASSES["period-context"];
  }

  if (!sectionDefault) {
    return CLASSES.unclassified;
  }
  // Root-gated defaults: a section default may never raise the epistemic
  // strength of the provenance root (C-4.1). "Recorded" requires a resolved
  // claim; "Annual daily average" requires a derived value.
  if (sectionDefault === "recorded" && !hasResolved) {
    return CLASSES.unclassified;
  }
  if (sectionDefault === "daily-average" && !hasDerived) {
    return CLASSES.unclassified;
  }
  // "App-derived comparison" is a claim about who made the claim. A
  // statement in this section rooted in a resolved claim is a source's
  // assertion, and labelling it app-derived misattributes it — the same
  // C-4.1 failure as the two gates above, in the one section that lacked
  // the check. The derived case is settled earlier, above the temporal
  // branches.
  if (sectionDefault === "comparison") {
    return CLASSES.unclassified;
  }
  return CLASSES[sectionDefault];
}
