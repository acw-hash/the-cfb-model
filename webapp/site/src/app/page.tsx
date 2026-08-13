import { notFound } from "next/navigation";

/** Root placeholder — real pages land in W3–W6. Gallery is dev-only. */
export default function HomePage(): never {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  notFound();
}
