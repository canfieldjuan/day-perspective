"use client";

import { PUBLIC_DATE_MAX, PUBLIC_DATE_MIN } from "@day-perspective/contracts";
import React, { type FormEvent, useRef, useState } from "react";

type Claim = {
  id: string;
  predicate: string;
  status: string;
  value: { value?: unknown; wikidata_reference_count?: number } | null;
  locator: string;
};
type Choice = {
  predicate: string;
  root_id: string;
  version: number;
  status: string | null;
  rank: number | null;
  rationale: string | null;
  text: string;
};
type Candidate = {
  release_id: string;
  label: string;
  entity_id: string;
  revision: number | null;
  source_url: string | null;
  checksum: string;
  claims: Claim[];
  preview: Choice[];
  can_resolve: boolean;
  can_publish: boolean;
  blocked: string | null;
};
type Outcome = {
  status: string;
  manifest_id?: string | null;
  occurrence_date?: string;
  merge_review_task_id?: string | null;
};
type ReviewRequest = <T>(path: string, init?: RequestInit) => Promise<T>;

function predicateLabel(predicate: string) {
  return predicate.replace(/^candidate_/, "").replaceAll("_", " ");
}

function CandidateEvidence({ candidate, busy, act }: {
  candidate: Candidate;
  busy: boolean;
  act: (action: string, body?: object) => void;
}) {
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [editorial, setEditorial] = useState("");
  const core = candidate.preview.filter((choice) => choice.predicate !== "candidate_coordinates");
  const source = candidate.source_url && URL.canParse(candidate.source_url)
    ? new URL(candidate.source_url) : null;

  return <>
    <h3>{candidate.label}</h3>
    <dl className="candidate-meta">
      <div><dt>Entity</dt><dd>{candidate.entity_id}</dd></div>
      <div><dt>Revision</dt><dd>{candidate.revision ?? "Unavailable"}</dd></div>
      <div><dt>Release</dt><dd>{candidate.release_id}</dd></div>
      <div><dt>Source URL</dt><dd>
        {source && ["https:", "http:"].includes(source.protocol)
          ? <a href={source.href} target="_blank" rel="noreferrer">{candidate.source_url}</a>
          : candidate.source_url ?? "Unavailable"}
      </dd></div>
      <div><dt>Checksum</dt><dd>{candidate.checksum}</dd></div>
    </dl>
    {candidate.blocked && <p role="alert">{candidate.blocked}</p>}
    <h4>Imported evidence</h4>
    <p>Review the pinned source before accepting a claim. Reference counts alone do not establish independent corroboration.</p>
    {candidate.claims.map((claim) => {
      const label = predicateLabel(claim.predicate);
      const rationale = notes[claim.id] ?? "";
      const references = claim.value?.wikidata_reference_count ?? 0;
      return <article className="candidate-claim" key={claim.id}>
        <h5>{label}</h5>
        <p>{claim.status.replaceAll("_", " ")}</p>
        <pre>{JSON.stringify(claim.value?.value ?? claim.value, null, 2)}</pre>
        <p className="admin-mono">{claim.locator}</p>
        <p>{references ? `${references} recorded ${references === 1 ? "reference" : "references"}` : "No references recorded."}</p>
        {["candidate", "in_review"].includes(claim.status) && <div className="candidate-actions">
          <label>Rationale for {label}
            <textarea value={rationale} disabled={busy}
              onChange={(event) => setNotes({ ...notes, [claim.id]: event.target.value })} />
          </label>
          {(["accepted", "rejected"] as const).map((decision) => <button
            key={decision} type="button" disabled={busy || !rationale.trim()}
            onClick={() => act(`claims/${claim.id}/decision`, { decision, rationale })}
          >{decision === "accepted" ? "Accept" : "Reject"} {label}</button>)}
        </div>}
      </article>;
    })}
    <div className="candidate-claim">
      <h4>Resolution</h4>
      <button type="button" disabled={busy || !candidate.can_resolve} onClick={() => act("resolve")}>
        Resolve Wikidata claims
      </button>
      {!candidate.can_resolve && !candidate.blocked && <p>Accept the identity, name, type and occurrence claims to resolve the event.</p>}
    </div>
    {!!core.length && <div className="candidate-claim">
      <h4>Editorial preview</h4>
      <ol>
        {core.map((choice) => <li key={choice.root_id}>
          {choice.text}
          <p>{choice.status ? `Current choice: ${choice.status}${choice.rank === null ? "" : `, rank ${choice.rank}`}. ${choice.rationale ?? ""}` : "No editorial choice recorded."}</p>
        </li>)}
      </ol>
      {candidate.preview.filter((choice) => choice.predicate === "candidate_coordinates" && choice.status === "selected").map((choice) =>
        <p key={choice.root_id}>Existing optional selection: {choice.text}</p>
      )}
      <div className="candidate-actions">
        <label>Editorial rationale
          <textarea value={editorial} disabled={busy} onChange={(event) => setEditorial(event.target.value)} />
        </label>
        <button type="button" disabled={busy || !editorial.trim()} onClick={() => act("select", {
          rationale: editorial,
          preview: core.map(({ root_id, version }) => ({ root_id, version }))
        })}>Select statements</button>
      </div>
    </div>}
    <div className="candidate-claim">
      <h4>Publication</h4>
      <button type="button" disabled={busy || !candidate.can_publish} onClick={() => act("publish")}>
        Publish selected release
      </button>
    </div>
  </>;
}

export function WikidataReview({ request, onMutation }: { request: ReviewRequest; onMutation: (busy: boolean) => void }) {
  const [day, setDay] = useState("");
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [releaseId, setReleaseId] = useState("");
  const [message, setMessage] = useState("");
  const [published, setPublished] = useState<{ id: string; day: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const mutating = useRef(false);
  const path = `wikidata/${day}`;
  const candidate = rows?.find((row) => row.release_id === releaseId);

  async function load(event: FormEvent) {
    event.preventDefault();
    if (mutating.current || !day) return;
    const current = ++generation.current;
    setRows(null);
    setReleaseId("");
    setPublished(null);
    setMessage("Loading candidates…");
    try {
      const { candidates } = await request<{ candidates: Candidate[] }>(path);
      if (current !== generation.current) return;
      setRows(candidates);
      setMessage(candidates.length ? "Choose a source release to review." : "No matching imported candidates for this date.");
    } catch (error) {
      if (current === generation.current) setMessage(String(error));
    }
  }

  async function act(action: string, body?: object) {
    if (mutating.current || !candidate) return;
    mutating.current = true;
    generation.current++;
    setBusy(true);
    onMutation(true);
    setPublished(null);
    setMessage("Saving review action…");
    let result: string;
    try {
      const outcome = await request<Outcome>(`${path}/${candidate.release_id}/${action}`, {
        method: "POST", body: JSON.stringify(body ?? {})
      });
      if (outcome.status === "published" && outcome.manifest_id && outcome.occurrence_date === day) {
        setPublished({ id: outcome.manifest_id, day });
        result = `Published manifest ${outcome.manifest_id}.`;
      } else if (outcome.status === "published") {
        result = "The publication response is missing a manifest for the selected date. Reload candidates to confirm the result.";
      } else if (outcome.status === "deferred_to_merge_review") {
        result = `Publication deferred for collision review. Task ${outcome.merge_review_task_id ?? "unavailable"}.`;
      } else if (action === "publish") {
        result = `Publication outcome: ${outcome.status.replaceAll("_", " ")}.`;
      } else {
        result = `Recorded ${outcome.status}.`;
      }
    } catch (error) {
      result = String(error);
    }
    try {
      const fresh = await request<{ candidates: Candidate[] }>(path);
      setRows(fresh.candidates);
      setMessage(result);
    } catch (error) {
      setRows(null);
      setMessage(`${result} Reload candidates to confirm current state. ${String(error)}`);
    } finally {
      mutating.current = false;
      setBusy(false);
      onMutation(false);
    }
  }

  return <section className="admin-panel" aria-labelledby="candidate-review-heading">
    <h2 id="candidate-review-heading">Wikidata candidate review</h2>
    <form className="candidate-actions" onSubmit={load}>
      <label>Occurrence date
        <input type="date" required min={PUBLIC_DATE_MIN} max={PUBLIC_DATE_MAX} value={day} disabled={busy}
          onChange={(event) => {
            if (mutating.current) return;
            generation.current++;
            setDay(event.target.value);
            setRows(null);
            setReleaseId("");
            setPublished(null);
            setMessage("");
          }} />
      </label>
      <button className="action-button" type="submit" disabled={busy}>Load candidates</button>
    </form>
    <p role="status" className="admin-status">{message}</p>
    {!!rows?.length && <label>Source release
      <select value={releaseId} disabled={busy} onChange={(event) => {
        if (mutating.current) return;
        setReleaseId(event.target.value);
        setPublished(null);
        setMessage("");
      }}>
        <option value="">Choose a release</option>
        {rows.map((row) => <option key={row.release_id} value={row.release_id}>
          {row.label} ({row.entity_id}, revision {row.revision ?? "unavailable"})
        </option>)}
      </select>
    </label>}
    {candidate && <CandidateEvidence key={candidate.release_id} candidate={candidate} busy={busy} act={act} />}
    {published && <p><a href={`/day/${published.day}`}>Open published profile for {published.day}</a></p>}
  </section>;
}
