import React, { useState } from "react";

import type { PublishedDayProfile } from "@day-perspective/contracts";
import { DAY_PROFILE_SECTIONS } from "@/src/lib/day-profile";
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
              statements.map((statement, statementIndex) => {
                const evidenceClass = deriveEvidenceClass(section.key, statement);
                const disputed =
                  (statement.provenance?.dissenting_claims.length ?? 0) > 0;
                const caveat = evidenceClass.caveat
                  ? evidenceClass.caveat.replace(
                      "{year}",
                      profileYear || "the year"
                    )
                  : null;
                const isLead =
                  section.key === leadSectionKey && statementIndex === 0;

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
              })
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
                      <React.Fragment key={entry.url + entry.name}>
                        {index > 0 ? "; " : ""}
                        <a href={entry.url}>{entry.name}</a>, published by{" "}
                        {entry.publisher}
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
