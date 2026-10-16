import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TradeX Dashboard",
  description: "Private market analysis dashboard. Analysis only, no order execution.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
