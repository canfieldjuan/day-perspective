import React, { useState } from "react";

import type { PublishedDayProfile } from "@day-perspective/contracts";
import { DAY_PROFILE_SECTIONS, readEventGroup } from "@/src/lib/day-profile";
import { deriveEvidenceClass } from "@/src/lib/evidence-class";
import { EvidencePanel } from "./EvidencePanel";

export type SectionAvailability = "loading" | "unpublished" | "api-error" | "published";

const sectionMessages: Record<Exclude<SectionAvailability, "published">, string> = {
  loading: "Checking whether evidence-backed content has been published for this section.",
  unpublished: "No evidence-backed content has been published for this section on this date.",
  "api-error":
    "Section content cannot be loaded until the internal profile service is available."
};

type ProfileSectionsProps = {
  availability: SectionAvailability;
  sections?: PublishedDayProfile["sections"];
  sectionStates?: PublishedDayProfile["section_states"];
  sourceAttribution?: PublishedDayProfile["source_attribution"];
  sourceAttributions?: PublishedDayProfile["source_attributions"];
  quality?: PublishedDayProfile["quality"];
  profileDate?: string;
  publicationManifestId?: string;
  publicationContentHash?: string;
};

type EventGroup = {
  key: string;
  title: string;
  featured: boolean;
  order: number;
  statements: import("@day-perspective/contracts").ProfileStatement[];
};

/**
 * Group recorded statements by the event they describe.
 *
 * Reads the typed metadata the payload carries rather than inferring boundaries
 * from array position — a reader should not have to notice that the sentences
 * changed subject to work out where one event ends.
 *
 * Returns null when grouping would tell the reader nothing: any other section,
 * or a recorded section whose statements do not all declare an event. Falling
 * back keeps profiles published before typed grouping rendering exactly as they
 * did, and a partially-attributed section renders flat rather than stranding
 * some statements outside a group.
 *
 * Uses `readEventGroup` — the same predicate the response boundary validates
 * with, so the two cannot disagree about what a usable group is. It stays used
 * here rather than trusting the boundary to have run: the fallback costs the
 * reader the grouping, while a property access on a malformed group costs them
 * the whole page.
 */
function groupByEvent(
  sectionKey: string,
  statements: import("@day-perspective/contracts").ProfileStatement[]
): EventGroup[] | null {
  if (sectionKey !== "recorded_on_this_date") return null;
  if (statements.length === 0) return null;
  const declared = statements.map((statement) =>
    readEventGroup(statement.event_group)
  );
  if (declared.some((group) => group === null)) {
    return null;
  }
  const groups = new Map<string, EventGroup>();
  statements.forEach((statement, index) => {
    const group = declared[index]!;
    const existing = groups.get(group.event_group_key);
    if (existing) {
      existing.statements.push(statement);
      return;
    }
    groups.set(group.event_group_key, {
      key: group.event_group_key,
      title: group.event_title,
      featured: group.featured,
      order: group.event_order,
      statements: [statement]
    });
  });
  // The published sequence lives in event_order; the key is opaque by contract,
  // so sorting on it would order secondary events by an accident of hashing.
  const ordered = Array.from(groups.values()).sort((left, right) => {
    if (left.featured !== right.featured) return left.featured ? -1 : 1;
    if (left.order !== right.order) return left.order - right.order;
    return left.key.localeCompare(right.key);
  });
  for (const group of ordered) {
    group.statements.sort(
      (left, right) =>
        (left.event_group?.predicate_order ?? 0) -
        (right.event_group?.predicate_order ?? 0)
    );
  }
  return ordered;
}

export function ProfileSections({
  availability,
  sections,
  sectionStates,
  sourceAttribution,
  sourceAttributions,
  quality,
  profileDate,
  publicationManifestId,
  publicationContentHash
}: ProfileSectionsProps) {
  const profileYear = profileDate?.slice(0, 4) ?? "";
  const [evidenceStatement, setEvidenceStatement] = useState<
    import("@day-perspective/contracts").ProfileStatement | null
  >(null);
  const evidenceQualityGrade =
    typeof evidenceStatement?.details?.quality_grade === "string"
      ? evidenceStatement.details.quality_grade
      : quality?.grade;
  // A profile published before typed attribution carries only the singular
  // field. Normalising here means the render path has one shape to handle.
  //
  // The plural field wins on presence, not on truthiness. An empty array is a
  // profile saying its evidence credits nobody; reading that as "absent" and
  // falling back would resurrect a source the current contract deliberately
  // left out — the false claim the plural field exists to stop making.
  const attributions =
    sourceAttributions !== undefined
      ? sourceAttributions
      : sourceAttribution
        ? [sourceAttribution]
        : [];
  const showIntegrity =
    availability === "published" &&
    Boolean(
      quality ||
        attributions.length > 0 ||
        publicationManifestId ||
        publicationContentHash
    );

  return (
    <>
      <div className="strata" aria-label="Day profile sections">
        {DAY_PROFILE_SECTIONS.map((section) => {
        const headingId = section.id + "-heading";
        const statements = sections?.[section.key];
        const sectionState = sectionStates?.[section.key];
        const recordedPopulated =
          availability === "published" &&
          (sections?.recorded_on_this_date?.length ?? 0) > 0;
        const typicalPopulated =
          availability === "published" &&
          (sections?.typical_day_in_this_year?.length ?? 0) > 0;
        const leadSectionKey = recordedPopulated
          ? "recorded_on_this_date"
          : typicalPopulated
            ? "typical_day_in_this_year"
            : null;
        const populated =
          availability === "published" &&
          statements !== undefined &&
          statements.length > 0;
        const stratumState = populated
          ? "populated"
          : availability !== "published"
            ? availability
            : sectionState?.status === "not_yet_supported"
              ? "not_yet_supported"
              : "empty";

        return (
          <section
            className={populated ? "stratum" : "stratum stratum--seam"}
            data-testid={"stratum-" + section.key}
            data-stratum-state={stratumState}
            key={section.key}
            aria-labelledby={headingId}
          >
            <h2 id={headingId}>{section.title}</h2>
            {populated ? (
              (() => {
              const renderStatement = (
                statement: import("@day-perspective/contracts").ProfileStatement,
                statementIndex: number,
                isLead: boolean
              ) => {
                const evidenceClass = deriveEvidenceClass(section.key, statement);
                const disputed =
                  (statement.provenance?.dissenting_claims.length ?? 0) > 0;
                const caveat = evidenceClass.caveat
                  ? evidenceClass.caveat.replace(
                      "{year}",
                      profileYear || "the year"
                    )
                  : null;

                return (
                <article
                  className="profile-statement"
                  data-evidence-class={evidenceClass.key}
                  {...(isLead ? { "data-lead": "true" } : {})}
                  key={statement.statement_id}
                >
                  <p className="evidence-chips">
                    <span
                      className="evidence-chip"
                      data-evidence-class={evidenceClass.key}
                      data-testid="evidence-chip"
                    >
                      {evidenceClass.label}
                    </span>
                    {disputed ? (
                      <span
                        className="evidence-chip evidence-chip--disputed"
                        data-testid="evidence-chip"
                      >
                        Disputed — sources disagree
                      </span>
                    ) : null}
                  </p>
                  <p className="statement-text">{statement.statement}</p>
                  {caveat ? <p className="evidence-caveat">{caveat}</p> : null}
                  {statement.provenance_note ? (
                    <p className="profile-statement__provenance">{statement.provenance_note}</p>
                  ) : null}
                  {statement.details?.quality_grade ? (
                    <p className="quality-grade">
                      Evidence quality: {String(statement.details.quality_grade)}
                    </p>
                  ) : null}
                  {statement.provenance ? (
                    <button
                      className="provenance-trigger"
                      onClick={() => setEvidenceStatement(statement)}
                      type="button"
                    >
                      Why can the app say this?
                    </button>
                  ) : null}
                </article>
                );
              };
              const groups = groupByEvent(section.key, statements);
              return groups ? (
                groups.map((group, groupIndex) => (
                  <React.Fragment key={group.key}>
                    {groupIndex === 1 ? (
                      <p className="stratum__group-lede">
                        Also recorded on this date
                      </p>
                    ) : null}
                    <div
                      className={
                        group.featured
                          ? "event-group event-group--featured"
                          : "event-group"
                      }
                      data-event-group={group.key}
                      data-featured={group.featured ? "true" : "false"}
                      data-testid={"event-group-" + group.key}
                    >
                      <h3 className="event-group__title">{group.title}</h3>
                      {group.statements.map((statement, index) =>
                        renderStatement(
                          statement,
                          index,
                          group.featured && index === 0
                        )
                      )}
                    </div>
                  </React.Fragment>
                ))
              ) : (
                <>
                  {statements.map((statement, statementIndex) =>
                    renderStatement(
                      statement,
                      statementIndex,
                      section.key === leadSectionKey && statementIndex === 0
                    )
                  )}
                </>
              );
            })()
            ) : (
              <>
                {availability === "published" &&
                section.key === "recorded_on_this_date" ? (
                  <p className="stratum__state">
                    No reviewed event is published for this date.
                  </p>
                ) : null}
                {availability !== "published" ? (
                  <p className="stratum__state">{sectionMessages[availability]}</p>
                ) : sectionState?.status === "not_yet_supported" ? (
                  <p className="stratum__state">
                    {sectionState.reason ??
                      "This section is not yet supported by an implemented pipeline."}
                  </p>
                ) : section.key !== "recorded_on_this_date" ? (
                  <p className="stratum__state">
                    No evidence-backed content was published for this section.
                  </p>
                ) : null}
              </>
            )}
            {section.key === "evidence_notes" && showIntegrity ? (
              <div
                className="publication-integrity"
                data-testid="publication-integrity"
              >
                <p className="eyebrow">Publication integrity</p>
                {quality ? (
                  <p>
                    <span className="quality-grade">Grade {quality.grade}</span>{" "}
                    {quality.explanation}
                  </p>
                ) : null}
                {attributions.length > 0 ? (
                  <p data-testid="source-attributions">
                    {attributions.length === 1 ? "Source: " : "Sources: "}
                    {attributions.map((entry, index) => (
                      <React.Fragment key={entry.name + (entry.url ?? "")}>
                        {index > 0 ? "; " : ""}
                        {/*
                          A source's publisher and URL are both nullable
                          upstream, and the payload omits the key entirely
                          for an absent one. Neither absence is dressed up:
                          an empty href resolves to the current page, so a
                          reader clicking a credit is silently reloaded
                          rather than sent to the source, and "published by
                          ." asserts an attribution the payload does not
                          carry.
                        */}
                        {entry.url !== undefined ? (
                          <a href={entry.url}>{entry.name}</a>
                        ) : (
                          entry.name
                        )}
                        {entry.publisher !== undefined
                          ? `, published by ${entry.publisher}`
                          : ""}
                      </React.Fragment>
                    ))}
                    .
                  </p>
                ) : null}
                {publicationManifestId ? (
                  <p className="integrity-mono">Manifest {publicationManifestId}</p>
                ) : null}
                {publicationContentHash ? (
                  <details>
                    <summary className="integrity-mono">
                      Content hash {publicationContentHash.slice(0, 12)}…
                    </summary>
                    <p className="integrity-mono">{publicationContentHash}</p>
                  </details>
                ) : null}
              </div>
            ) : null}
          </section>
        );
        })}
      </div>
      <EvidencePanel
        onClose={() => setEvidenceStatement(null)}
        open={evidenceStatement !== null}
        qualityGrade={evidenceQualityGrade}
        statement={evidenceStatement ?? undefined}
      />
    </>
  );
}
