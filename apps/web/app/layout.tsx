import type { Metadata } from "next";
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

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
