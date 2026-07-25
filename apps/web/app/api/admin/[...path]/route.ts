import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

function getApiBaseUrl(): string {
  return (process.env.API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
}

async function forward(request: Request, path: string[]): Promise<Response> {
  const token = request.headers.get("x-development-review-token");
  if (!token) {
    return NextResponse.json(
      {
        detail:
          "A development review token is required. This is not production authentication."
      },
      { status: 403, headers: { "Cache-Control": "no-store" } }
    );
  }
  const headers = new Headers({
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Development-Review-Token": token
  });
  const body = request.method === "POST" ? await request.text() : undefined;
  try {
    const upstream = await fetch(
      `${getApiBaseUrl()}/api/v1/admin/${path.map(encodeURIComponent).join("/")}`,
      {
        method: request.method,
        headers,
        body,
        cache: "no-store"
      }
    );
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type":
          upstream.headers.get("content-type") || "application/json"
      }
    });
  } catch {
    return NextResponse.json(
      { detail: "The internal review API is unavailable." },
      { status: 503, headers: { "Cache-Control": "no-store" } }
    );
  }
}

export async function GET(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return forward(request, (await context.params).path);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ path: string[] }> }
) {
  return forward(request, (await context.params).path);
}
