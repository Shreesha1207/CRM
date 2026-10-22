import { useId, useRef, useState } from "react";
import { Close } from "./icons";
import { Badge, Input, cx } from "./ui";

export interface LinkOption {
  id: string;
  name: string;
  /** A short second line in the suggestions (a type, a length…). */
  detail?: string;
  inactive?: boolean;
}

/**
 * Links things the way a profile links a certification to its issuer: type a few
 * letters, pick an existing one from the suggestions, or create it on the spot when
 * it doesn't exist yet. Linked items show as chips that can be removed.
 */
export function LinkPicker({
  label,
  options,
  selected,
  onAdd,
  onRemove,
  onCreate,
  placeholder,
  emptyText = "Nothing linked yet.",
}: {
  label: string;
  options: LinkOption[];
  selected: string[];
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
  /** Offered when what was typed matches nothing; receives the typed name. */
  onCreate?: (name: string) => void;
  placeholder?: string;
  emptyText?: string;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const byId = new Map(options.map((o) => [o.id, o]));
  const text = query.trim();
  const needle = text.toLowerCase();
  const matches = options.filter((o) => !selected.includes(o.id) && (!needle || o.name.toLowerCase().includes(needle))).slice(0, 8);
  const exact = options.some((o) => o.name.toLowerCase() === needle);
  const canCreate = !!onCreate && !!text && !exact;
  const count = matches.length + (canCreate ? 1 : 0);

  const choose = (index: number) => {
    if (index < matches.length) onAdd(matches[index].id);
    else if (canCreate) onCreate!(text);
    else return;
    setQuery("");
    setActive(0);
    input.current?.focus();
  };

  return (
    <div className="space-y-2">
      <label htmlFor={id} className="block text-sm font-medium text-fg">
        {label}
      </label>
      {selected.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label={label}>
          {selected.map((sid) => {
            const option = byId.get(sid);
            return (
              <li key={sid} className="inline-flex items-center gap-1 rounded-full border border-line-strong bg-subtle py-0.5 pl-2.5 pr-1 text-sm text-fg">
                <span>{option?.name ?? "…"}</span>
                {option?.inactive && <Badge>inactive</Badge>}
                <button
                  type="button"
                  onClick={() => onRemove(sid)}
                  aria-label={`Remove ${option?.name ?? "item"}`}
                  className="inline-flex h-5 w-5 items-center justify-center rounded-full text-muted hover:bg-line hover:text-fg"
                >
                  <Close className="h-3 w-3" />
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-sm text-faint">{emptyText}</p>
      )}
      <div className="relative">
        <Input
          ref={input}
          id={id}
          role="combobox"
          aria-expanded={open && count > 0}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={open && count > 0 ? `${id}-opt-${active}` : undefined}
          autoComplete="off"
          placeholder={placeholder}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          // Let a click on a suggestion land before the list closes.
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setActive((a) => (count ? (a + 1) % count : 0));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => (count ? (a - 1 + count) % count : 0));
            } else if (e.key === "Enter") {
              if (open && count) {
                e.preventDefault();
                choose(active);
              }
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        {open && count > 0 && (
          <ul
            id={`${id}-list`}
            role="listbox"
            className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-md border border-line-strong bg-surface py-1 shadow-lg"
          >
            {matches.map((o, i) => (
              <li
                key={o.id}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(i)}
                className={cx("cursor-pointer px-3 py-1.5 text-sm", i === active ? "bg-accent-soft" : "hover:bg-subtle")}
              >
                <span className="font-medium text-fg">{o.name}</span>
                {o.detail && <span className="ml-2 text-xs text-muted">{o.detail}</span>}
                {o.inactive && <span className="ml-2 text-xs text-faint">inactive</span>}
              </li>
            ))}
            {canCreate && (
              <li
                id={`${id}-opt-${matches.length}`}
                role="option"
                aria-selected={active === matches.length}
                onMouseDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(matches.length)}
                onClick={() => choose(matches.length)}
                className={cx(
                  "cursor-pointer border-t border-line px-3 py-1.5 text-sm text-accent-text",
                  active === matches.length ? "bg-accent-soft" : "hover:bg-subtle",
                )}
              >
                + Create “{text}”
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}
