import type { Metadata } from "next";
import { Bricolage_Grotesque, DM_Sans, IBM_Plex_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";

const display = Bricolage_Grotesque({
  variable: "--font-display",
  subsets: ["latin"],
});

const body = DM_Sans({
  variable: "--font-body",
  subsets: ["latin"],
});

const utility = IBM_Plex_Mono({
  variable: "--font-utility",
  subsets: ["latin"],
  weight: ["500", "600"],
});

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host");
  const protocol = requestHeaders.get("x-forwarded-proto") ?? "https";
  const base = host ? `${protocol}://${host}` : "http://localhost:3000";
  const description =
    "Track, reschedule, and cancel class deadlines in Discord without AI. Optional AI organizes study sets and creates private practice quizzes. Built by Emma Hsieh.";
  return {
    title: {
      default: "Memlo — The group chat that remembers",
      template: "%s — Memlo",
    },
    description,
    openGraph: {
      title: "Memlo — The group chat that remembers.",
      description,
      images: [{ url: `${base}/og-v2.png`, width: 1731, height: 909 }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Memlo — The group chat that remembers.",
      description,
      images: [`${base}/og-v2.png`],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={[display.variable, body.variable, utility.variable].join(" ")}>
        {children}
      </body>
    </html>
  );
}
