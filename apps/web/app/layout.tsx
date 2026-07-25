import type { Metadata } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/landing.css";
import "./styles/profile.css";
import "./styles/admin.css";

export const metadata: Metadata = {
  title: "Day Perspective",
  description: "Evidence-led historical perspective organized around calendar dates."
};

const displayFont = localFont({
  src: "./fonts/fraunces-latin-wght-normal.woff2",
  display: "swap",
  variable: "--font-display",
  adjustFontFallback: "Times New Roman"
});

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={displayFont.variable}>
      <body>{children}</body>
    </html>
  );
}
