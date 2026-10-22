// The same rules the server applies: 7 to 15 digits (the international maximum),
// optionally after a + and a country code; spaces, dashes, dots and brackets are fine.

/** Drops anything that can never be part of a phone number, as it is typed. */
export function phoneChars(value: string): string {
  return value.replace(/[^\d\s().+-]/g, "");
}

/** Why `value` is not a phone number, or null when it is one (or is empty). */
export function phoneProblem(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  if (!/^\+?[\d\s().-]+$/.test(v)) return "Use digits only, with an optional + and country code (e.g. +91 98765 43210)";
  const digits = v.replace(/\D/g, "").length;
  if (digits < 7 || digits > 15) return "A phone number has 7 to 15 digits";
  return null;
}
