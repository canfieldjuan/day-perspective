"use client";

import React, { useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  PUBLIC_DATE_MAX,
  PUBLIC_DATE_MIN,
  isSupportedPublicDate
} from "@/src/lib/date";
import { markNavigation } from "@/src/lib/travel-store";

type DateInputFormProps = {
  initialDate?: string;
};

export function DateInputForm({ initialDate = "" }: DateInputFormProps) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const date = String(formData.get("date") || "");

    if (!isSupportedPublicDate(date)) {
      setError(
        "Enter a valid date from 1900-01-01 through 2025-12-31 before opening a profile."
      );
      return;
    }

    setError(null);
    markNavigation();
    router.push("/day/" + date);
  }

  return (
    <form className="date-form" onSubmit={handleSubmit} noValidate>
      <div className="date-field">
        <label htmlFor="historical-date">Date</label>
        <input
          aria-describedby={error ? "date-form-error" : undefined}
          defaultValue={initialDate}
          id="historical-date"
          max={PUBLIC_DATE_MAX}
          min={PUBLIC_DATE_MIN}
          name="date"
          required
          type="date"
        />
      </div>
      <button className="action-button" type="submit">
        Open profile
      </button>
      {error ? (
        <p className="form-error" id="date-form-error" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
