import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "PFIA Frontend",
  description:
    "Next.js frontend for Product Feedback Intelligence Agent upload, report, and grounded Q&A."
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
