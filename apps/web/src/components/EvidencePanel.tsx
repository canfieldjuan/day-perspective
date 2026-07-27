"use client";

import React, { useEffect, useRef } from "react";

import type { ProfileStatement } from "@day-perspective/contracts";
import styles from "./EvidencePanel.module.css";
import { modelCardUrl } from "@/src/lib/model-card";

type EvidencePanelProps = {
  open: boolean;
  statement?: ProfileStatement;
  qualityGrade?: string;
  onClose: () => void;
};

/**
 * Modal evidence panel (UI_UX_CONTRACT C-9): native <dialog> supplies focus
 * containment, Esc handling, and focus restoration to the trigger. Renders
 * only payload-provided provenance; dissenting records get the same
 * completeness as supporting ones.
 */
export function EvidencePanel({
  open,
  statement,
  qualityGrade,
  onClose
}: EvidencePanelProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }
    if (open && !dialog.open) {
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  const provenance = statement?.provenance;
  // Read from the derived value the panel already shows, so the card named
  // here is the one that governs the computation on screen rather than a
  // label attached alongside it.
  const modelCardValue = provenance?.derived_value?.value?.model_card;
  const modelCard =
    typeof modelCardValue === "string" && modelCardValue ? modelCardValue : null;

  return (
    <dialog
      aria-label="Evidence for this statement"
      className={styles.panel}
      data-testid="evidence-panel"
      onCancel={onClose}
      onClose={onClose}
      ref={dialogRef}
    >
      {statement && provenance ? (
        <div className={styles.body}>
          <p className="eyebrow">Evidence</p>
          <p className={styles.statement}>{statement.statement}</p>
          <dl className={styles.chain}>
            {provenance.resolved_claim ? (
              <>
                <dt>Resolved claim</dt>
                <dd>
                  {provenance.resolved_claim.canonical_key}, version{" "}
                  {provenance.resolved_claim.version}, method{" "}
                  {provenance.resolved_claim.method}:{" "}
                  {provenance.resolved_claim.rationale}
                </dd>
              </>
            ) : null}
            {provenance.derived_value ? (
              <>
                <dt>Derived value</dt>
                <dd>
                  {provenance.derived_value.kind}, calculation version{" "}
                  {provenance.derived_value.calculation_version}
                </dd>
              </>
            ) : null}
            {/* A comparison the application made carries the card that says
                what it must not be read as. Naming it here is what makes
                "no comparison ships without one" reachable by a reader
                rather than a promise kept in the repository. */}
            {modelCard ? (
              <>
                <dt>Model card</dt>
                <dd>
                  <a href={modelCardUrl(modelCard)} rel="noreferrer">
                    {modelCard}
                  </a>
                </dd>
              </>
            ) : null}
            <dt>Why published</dt>
            <dd>{provenance.published_statement}</dd>
            <dt>Supporting claims</dt>
            <dd>
              {provenance.supporting_claims.length === 0
                ? "None recorded."
                : provenance.supporting_claims.map((claim, index) => (
                    <span key={`s:${claim.predicate}:${index}`}>
                      {claim.predicate} from{" "}
                      <a href={claim.source_record_locator}>
                        the {provenance.source_release.source} source record
                      </a>
                    </span>
                  ))}
            </dd>
            <dt>Dissenting claims</dt>
            <dd>
              {provenance.dissenting_claims.length === 0
                ? "None in this publication."
                : provenance.dissenting_claims.map((claim, index) => (
                    <span key={`d:${claim.predicate}:${index}`}>
                      {claim.predicate} from{" "}
                      <a href={claim.source_record_locator}>
                        the dissenting {provenance.source_release.source} source
                        record
                      </a>
                    </span>
                  ))}
            </dd>
            <dt>Source release</dt>
            <dd>
              {provenance.source_release.source}:{" "}
              {provenance.source_release.release}
              {provenance.source_release.publisher
                ? ", published by " + provenance.source_release.publisher
                : ""}
              . Retrieved {provenance.source_release.retrieved_at}.
            </dd>
            {qualityGrade ? (
              <>
                <dt>Evidence quality</dt>
                <dd>Grade {qualityGrade}</dd>
              </>
            ) : null}
            <dt>Methodology</dt>
            <dd>
              {provenance.methodology.name}, version{" "}
              {provenance.methodology.version}:{" "}
              {provenance.methodology.description}
            </dd>
          </dl>
          <button
            aria-label="Close evidence panel"
            className="action-button"
            onClick={onClose}
            type="button"
          >
            Close
          </button>
        </div>
      ) : null}
    </dialog>
  );
}
