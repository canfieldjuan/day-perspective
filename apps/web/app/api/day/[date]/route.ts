import { NextResponse } from "next/server";
import { isSupportedPublicDate } from "@/src/lib/date";

export const dynamic = "force-dynamic";

function getApiBaseUrl(): string {
  return (process.env.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ date: string }> }
) {
  const { date } = await context.params;

  if (!isSupportedPublicDate(date)) {
    return NextResponse.json(
      {
        status: "invalid_date",
        message: "Dates must be valid ISO calendar dates from 1900-01-01 through 2025-12-31."
      },
      { status: 400, headers: { "Cache-Control": "no-store" } }
    );
  }

  const apiUrl = getApiBaseUrl() + "/api/v1/day/" + encodeURIComponent(date);

  try {
    const upstream = await fetch(apiUrl, {
      cache: "no-store",
      headers: {
        Accept: "application/json"
      }
    });
    const body = await upstream.text();
    const headers = new Headers({
      "Cache-Control": "no-store"
    });
    const contentType = upstream.headers.get("content-type");

    if (contentType) {
      headers.set("content-type", contentType);
    }

    return new Response(body, {
      headers,
      status: upstream.status
    });
  } catch {
    return NextResponse.json(
      {
        status: "api_unavailable",
        message: "The internal profile service is unavailable."
      },
      { status: 503, headers: { "Cache-Control": "no-store" } }
    );
  }
}
