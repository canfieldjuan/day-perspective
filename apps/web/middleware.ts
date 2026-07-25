import { NextResponse, type NextRequest } from "next/server";

import { canonicalizePublicDatePath } from "@/src/lib/date";

/**
 * Canonicalize /day/<date> paths with a real HTTP 308 before any rendering
 * or streaming starts (UI_UX_CONTRACT C-6.4). A page-level redirect would
 * degrade to a meta refresh once the segment's loading boundary begins
 * streaming, which non-browser clients do not follow.
 */
export function middleware(request: NextRequest) {
  const match = /^\/day\/([^/]+)$/.exec(request.nextUrl.pathname);
  if (!match) {
    return NextResponse.next();
  }
  const canonical = canonicalizePublicDatePath(decodeURIComponent(match[1]));
  if (!canonical) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  url.pathname = "/day/" + canonical;
  return NextResponse.redirect(url, 308);
}

export const config = {
  matcher: "/day/:path*"
};
