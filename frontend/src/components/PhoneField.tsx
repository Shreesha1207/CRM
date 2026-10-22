import { useState } from "react";
import { phoneChars, phoneProblem } from "../lib/phone";
import { Field, Input } from "./ui";

/** A phone number field: only phone characters can be typed, and a number that
 * can't be right is pointed out once the person leaves the field. */
export function PhoneField({ label = "Phone", value, onChange }: { label?: string; value: string; onChange: (value: string) => void }) {
  const [touched, setTouched] = useState(false);
  const problem = touched ? phoneProblem(value) : null;
  return (
    <Field label={label} hint={problem ? undefined : "With the country code, e.g. +91 98765 43210."} error={problem ?? undefined}>
      <Input
        type="tel"
        inputMode="tel"
        autoComplete="tel"
        maxLength={50}
        placeholder="+91 98765 43210"
        value={value}
        aria-invalid={!!problem}
        onChange={(e) => onChange(phoneChars(e.target.value))}
        onBlur={() => setTouched(true)}
      />
    </Field>
  );
}
